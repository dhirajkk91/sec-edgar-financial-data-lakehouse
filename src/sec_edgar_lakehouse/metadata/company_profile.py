"""Company name, tickers and industry come from the saved SEC submissions response.

Bronze discovery saves that response as submissions.json beside each
company-run record, together with its size and SHA-256. This loader checks
those bytes again, reads the company fields, and stores one row per company
and response checksum in silver.company_profiles.

Nothing is typed in by hand or fetched from another source. A rerun with the
same response returns ALREADY_EXISTS; a newer response adds a new snapshot
instead of overwriting the old one, so dbt can choose the latest.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb

_PROFILE_SCHEMA = (
    ("cik", "VARCHAR", "NO"),
    ("company_name", "VARCHAR", "NO"),
    ("tickers", "VARCHAR[]", "NO"),
    ("exchanges", "VARCHAR[]", "NO"),
    ("sic_code", "VARCHAR", "YES"),
    ("sic_description", "VARCHAR", "YES"),
    ("owner_org", "VARCHAR", "YES"),
    ("entity_type", "VARCHAR", "YES"),
    ("filer_category", "VARCHAR", "YES"),
    ("fiscal_year_end", "VARCHAR", "YES"),
    ("state_of_incorporation", "VARCHAR", "YES"),
    ("profile_run_id", "VARCHAR", "NO"),
    ("run_record_path", "VARCHAR", "NO"),
    ("submissions_path", "VARCHAR", "NO"),
    ("submissions_url", "VARCHAR", "NO"),
    ("submissions_retrieved_at", "TIMESTAMP WITH TIME ZONE", "NO"),
    ("submissions_size_bytes", "BIGINT", "NO"),
    ("submissions_sha256", "VARCHAR", "NO"),
)


class CompanyProfileError(Exception):
    """The saved evidence or the database prevented a verified profile load."""


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """Company fields from one saved SEC submissions response, with its provenance."""

    cik: str
    company_name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    sic_code: str | None
    sic_description: str | None
    owner_org: str | None
    entity_type: str | None
    filer_category: str | None
    fiscal_year_end: str | None
    state_of_incorporation: str | None
    profile_run_id: str
    run_record_path: Path
    submissions_path: Path
    submissions_url: str
    submissions_retrieved_at: datetime
    submissions_size_bytes: int
    submissions_sha256: str


@dataclass(frozen=True, slots=True)
class CompanyProfileLoadResult:
    """Whether this response was new to the database, and the profile it holds."""

    outcome: Literal["INSERTED", "ALREADY_EXISTS"]
    database_path: Path
    profile: CompanyProfile


def _text(data: dict[str, Any], key: str) -> str | None:
    """Return a trimmed string, or None when SEC left the field out or empty."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CompanyProfileError(f"Submissions field {key!r} must be text")
    return value.strip() or None


def _text_list(data: dict[str, Any], key: str) -> tuple[str, ...]:
    """Return a list of non-empty strings; a missing field is an empty list."""
    value = data.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise CompanyProfileError(f"Submissions field {key!r} must be a list of text")
    return tuple(item.strip() for item in value)


def _read_profile(run_record_path: Path) -> CompanyProfile:
    """Check the run record and saved response bytes, then read the company fields."""
    run_directory = run_record_path.parent
    submissions_path = run_directory / "submissions.json"
    for file in (run_record_path, submissions_path):
        if file.is_symlink() or not file.is_file():
            raise CompanyProfileError(f"Evidence must be a regular file: {file}")
    if run_record_path.name != "run.json":
        raise CompanyProfileError("Run record must be named run.json")
    record = json.loads(run_record_path.read_bytes())
    if not isinstance(record, dict) or record.get("schema_version") != "1":
        raise CompanyProfileError('Run record requires schema version "1"')
    cik = record.get("cik")
    run_id = record.get("run_id")
    if not isinstance(cik, str) or re.fullmatch(r"[0-9]{10}", cik) is None:
        raise CompanyProfileError("Recorded CIK must be 10 digits")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id) is None
    ):
        raise CompanyProfileError("Run ID must be a safe ASCII identifier")
    if (
        run_directory.name != f"run_id={run_id}"
        or run_directory.parent.name != f"cik={cik}"
    ):
        raise CompanyProfileError("Run record does not match its evidence directories")

    evidence = record.get("submissions_evidence")
    if not isinstance(evidence, dict) or evidence.get("path") != "submissions.json":
        raise CompanyProfileError("Run record requires submissions.json evidence")
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    if evidence.get("url") != url:
        raise CompanyProfileError("Submissions URL must be canonical for the CIK")
    retrieved_text = evidence.get("retrieved_at")
    if not isinstance(retrieved_text, str):
        raise CompanyProfileError("Submissions retrieval time is missing")
    retrieved_at = datetime.fromisoformat(retrieved_text)
    if retrieved_at.utcoffset() != timedelta(0):
        raise CompanyProfileError("Submissions retrieval time must be UTC")
    size = evidence.get("size_bytes")
    sha256 = evidence.get("sha256")
    if type(size) is not int or size <= 0:
        raise CompanyProfileError("Submissions size must be a positive integer")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise CompanyProfileError("Submissions SHA-256 must be 64 hex characters")

    # The bytes must be exactly the ones recorded when the response was saved.
    content = submissions_path.read_bytes()
    if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
        raise CompanyProfileError("Submissions bytes do not match recorded checksum")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise CompanyProfileError("Submissions response must be a JSON object")

    # SEC sends the CIK as text; some saved fixtures use a number.
    response_cik = data.get("cik")
    if isinstance(response_cik, int) and not isinstance(response_cik, bool):
        response_cik = str(response_cik)
    if not isinstance(response_cik, str) or response_cik.zfill(10) != cik:
        raise CompanyProfileError("Submissions CIK does not match the run record")
    name = _text(data, "name")
    if name is None:
        raise CompanyProfileError("Submissions response has no company name")
    sic_code = _text(data, "sic")
    if sic_code is not None and re.fullmatch(r"[0-9]{3,4}", sic_code) is None:
        raise CompanyProfileError("SIC code must be 3 or 4 digits")
    fiscal_year_end = _text(data, "fiscalYearEnd")
    if (
        fiscal_year_end is not None
        and re.fullmatch(r"[0-9]{4}", fiscal_year_end) is None
    ):
        raise CompanyProfileError("Fiscal year end must be MMDD")

    return CompanyProfile(
        cik=cik,
        company_name=name,
        tickers=_text_list(data, "tickers"),
        exchanges=_text_list(data, "exchanges"),
        sic_code=sic_code,
        sic_description=_text(data, "sicDescription"),
        owner_org=_text(data, "ownerOrg"),
        entity_type=_text(data, "entityType"),
        filer_category=_text(data, "category"),
        fiscal_year_end=fiscal_year_end,
        state_of_incorporation=_text(data, "stateOfIncorporation"),
        profile_run_id=run_id,
        run_record_path=run_record_path.resolve(strict=True),
        submissions_path=submissions_path.resolve(strict=True),
        submissions_url=url,
        submissions_retrieved_at=retrieved_at,
        submissions_size_bytes=size,
        submissions_sha256=sha256,
    )


def _ensure_table(connection: duckdb.DuckDBPyConnection) -> None:
    """Create silver.company_profiles, or check an existing one has the same shape."""
    connection.execute("CREATE SCHEMA IF NOT EXISTS silver")
    exists = connection.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_catalog=current_database() AND table_schema='silver' "
        "AND table_name='company_profiles'"
    ).fetchone()
    if exists is None:
        columns = ", ".join(
            f'"{name}" {kind}' + (" NOT NULL" if nullable == "NO" else "")
            for name, kind, nullable in _PROFILE_SCHEMA
        )
        connection.execute(
            f"CREATE TABLE silver.company_profiles ({columns}, "
            "PRIMARY KEY (cik, submissions_sha256))"
        )
        return
    actual = tuple(
        row[:3]
        for row in connection.execute("DESCRIBE silver.company_profiles").fetchall()
    )
    if exists[0] != "BASE TABLE" or actual != _PROFILE_SCHEMA:
        raise CompanyProfileError("Incompatible silver.company_profiles schema")


def load_company_profile(
    *, run_record_path: Path, database_path: Path
) -> CompanyProfileLoadResult:
    """Store the company profile from one verified company-run record.

    The database must already exist. The evidence is checked before the
    database is opened, and the insert runs in a transaction.
    """
    if not isinstance(run_record_path, Path) or not isinstance(database_path, Path):
        raise CompanyProfileError("Run record and database must be pathlib.Path values")
    try:
        profile = _read_profile(run_record_path)
    except (OSError, ValueError) as exc:
        raise CompanyProfileError(f"Invalid company profile evidence: {exc}") from exc
    try:
        database = database_path.resolve(strict=True)
        if not database.is_file():
            raise CompanyProfileError("Database must be an existing file")
        with duckdb.connect(str(database)) as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                _ensure_table(connection)
                stored = connection.execute(
                    "SELECT 1 FROM silver.company_profiles "
                    "WHERE cik=? AND submissions_sha256=?",
                    [profile.cik, profile.submissions_sha256],
                ).fetchone()
                if stored is None:
                    connection.execute(
                        "INSERT INTO silver.company_profiles VALUES "
                        f"({', '.join('?' for _ in _PROFILE_SCHEMA)})",
                        [
                            profile.cik,
                            profile.company_name,
                            list(profile.tickers),
                            list(profile.exchanges),
                            profile.sic_code,
                            profile.sic_description,
                            profile.owner_org,
                            profile.entity_type,
                            profile.filer_category,
                            profile.fiscal_year_end,
                            profile.state_of_incorporation,
                            profile.profile_run_id,
                            str(profile.run_record_path),
                            str(profile.submissions_path),
                            profile.submissions_url,
                            profile.submissions_retrieved_at,
                            profile.submissions_size_bytes,
                            profile.submissions_sha256,
                        ],
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
    except (OSError, duckdb.Error) as exc:
        raise CompanyProfileError(f"Company profile database error: {exc}") from exc
    return CompanyProfileLoadResult(
        "INSERTED" if stored is None else "ALREADY_EXISTS", database, profile
    )
