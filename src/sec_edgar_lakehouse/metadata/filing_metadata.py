"""Forms and filing/report dates come from the saved SEC company response.

This checks that response against its company-run record, then matches
it to active Silver filings. Reruns compare existing fields rather than
replacing the source and run details that first loaded them.
"""

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb

from sec_edgar_lakehouse.bronze.company_filings import (
    CompanyFilingsDiscovery,
    HistoryListing,
    _invalid_constant,
    _object,
    _parse,
    _parse_history,
)
from sec_edgar_lakehouse.filing_reference import _normalize_cik

_METADATA_SCHEMA = (
    ("cik", "VARCHAR", "NO"),
    ("accession_number", "VARCHAR", "NO"),
    ("form", "VARCHAR", "NO"),
    ("filing_date", "DATE", "NO"),
    ("report_date", "DATE", "YES"),
    ("primary_document", "VARCHAR", "NO"),
    ("metadata_run_id", "VARCHAR", "NO"),
    ("run_record_path", "VARCHAR", "NO"),
    ("submissions_path", "VARCHAR", "NO"),
    ("submissions_url", "VARCHAR", "NO"),
    ("submissions_retrieved_at", "TIMESTAMP WITH TIME ZONE", "NO"),
    ("submissions_size_bytes", "BIGINT", "NO"),
    ("submissions_sha256", "VARCHAR", "NO"),
)
_BUSINESS_FIELDS = ("form", "filing_date", "report_date", "primary_document")


class FilingMetadataError(Exception):
    """Evidence or database state prevented verified metadata loading."""


@dataclass(frozen=True, slots=True)
class FilingMetadataIssue:
    """An active filing missing from the listing, or fields that disagree with storage."""

    cik: str
    accession_number: str
    reason_code: Literal["MISSING_METADATA", "METADATA_CONFLICT"]
    message: str


@dataclass(frozen=True, slots=True)
class FilingMetadataLoadResult:
    """The metadata counts and issues for one company; this is not the run status."""

    status: Literal["COMPLETE", "PARTIAL"]
    database_path: Path
    cik: str
    source_run_id: str
    active_filing_count: int
    inserted_count: int
    already_existing_count: int
    missing_count: int
    conflict_count: int
    issues: tuple[FilingMetadataIssue, ...]


@dataclass(frozen=True, slots=True)
class _Evidence:
    """The checked company response and run fields needed to load filing metadata."""

    discovery: CompanyFilingsDiscovery
    run_id: str
    run_path: Path
    submissions_path: Path
    size_bytes: int
    sha256: str
    history_sources: dict[str, tuple[Path, HistoryListing, int, str]]


def _read_evidence(path: Path) -> _Evidence:
    """Check the run IDs, expected paths and original saved company response."""
    submissions = path.with_name("submissions.json")
    for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
        if directory.is_symlink() or not directory.is_dir():
            raise FilingMetadataError(
                f"Managed evidence directory must be real: {directory}"
            )
    for file in (path, submissions):
        if file.is_symlink() or not file.is_file():
            raise FilingMetadataError(
                f"Evidence must be a regular non-symlink file: {file}"
            )
    if path.name != "run.json":
        raise FilingMetadataError("Run record must be named run.json")
    record = json.loads(
        path.read_bytes(), object_pairs_hook=_object, parse_constant=_invalid_constant
    )
    if not isinstance(record, dict) or record.get("schema_version") != "1":
        raise FilingMetadataError('Run record requires schema version "1"')
    cik = record.get("cik")
    if not isinstance(cik, str) or _normalize_cik(cik) != cik:
        raise FilingMetadataError("Recorded CIK must be normalized")
    run_id = record.get("run_id")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id) is None
    ):
        raise FilingMetadataError("Run ID must be a safe ASCII identifier")
    if (
        path.parent.name != f"run_id={run_id}"
        or path.parent.parent.name != f"cik={cik}"
    ):
        raise FilingMetadataError(
            "Run record identity does not match evidence directories"
        )
    evidence = record.get("submissions_evidence")
    if not isinstance(evidence, dict) or evidence.get("path") != "submissions.json":
        raise FilingMetadataError("Run record requires submissions.json evidence")
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    if evidence.get("url") != url:
        raise FilingMetadataError(
            "Submissions URL must be canonical for the recorded CIK"
        )
    timestamp = evidence.get("retrieved_at")
    if not isinstance(timestamp, str):
        raise FilingMetadataError("Submissions retrieval timestamp must be UTC")
    retrieved = datetime.fromisoformat(timestamp)
    if retrieved.tzinfo is None or retrieved.utcoffset() != timedelta(0):
        raise FilingMetadataError(
            "Submissions retrieval timestamp must be timezone-aware UTC"
        )
    size = evidence.get("size_bytes")
    sha256 = evidence.get("sha256")
    if type(size) is not int or size <= 0:
        raise FilingMetadataError("Submissions size must be a positive integer")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise FilingMetadataError(
            "Submissions SHA-256 must be 64 lowercase hexadecimal characters"
        )
    content = submissions.read_bytes()
    if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
        raise FilingMetadataError(
            "Submissions bytes do not match recorded size and SHA-256"
        )
    history_records = record.get("history_evidence", [])
    if not isinstance(history_records, list):
        raise FilingMetadataError("History evidence must be an array")
    selection = record.get("selection", {})
    if not isinstance(selection, dict):
        raise FilingMetadataError("Run selection must be an object")
    history_since_text = selection.get("history_since")
    history_since = (
        date.fromisoformat(history_since_text)
        if isinstance(history_since_text, str)
        else None
    )
    if history_since_text is not None and history_since is None:
        raise FilingMetadataError("History cutoff must be a date")
    discovery = _parse(
        content,
        cik,
        url,
        retrieved.astimezone(UTC),
        include_history=bool(history_records),
    )
    expected_names = {
        item.name
        for item in discovery.history_files
        if history_since is None or item.filing_to >= history_since
    }
    if history_records and (
        len(history_records) != len(expected_names)
        or any(not isinstance(item, dict) for item in history_records)
        or {item.get("name") for item in history_records} != expected_names
    ):
        raise FilingMetadataError(
            "History evidence does not match submissions manifest"
        )
    history_sources: dict[str, tuple[Path, HistoryListing, int, str]] = {}
    listings = []
    seen = {
        filing.reference.accession_number: filing for filing in discovery.recent_filings
    }
    for reference in discovery.history_files:
        if reference.name not in expected_names:
            continue
        item = next(item for item in history_records if item["name"] == reference.name)
        relative = f"history/{reference.name}"
        history_path = path.parent / "history" / reference.name
        if (
            item.get("path") != relative
            or item.get("url") != f"https://data.sec.gov/submissions/{reference.name}"
        ):
            raise FilingMetadataError("History evidence path or URL is not canonical")
        if (
            history_path.parent.is_symlink()
            or not history_path.parent.is_dir()
            or history_path.is_symlink()
            or not history_path.is_file()
        ):
            raise FilingMetadataError(
                "History evidence must be a regular non-symlink file"
            )
        timestamp = item.get("retrieved_at")
        if not isinstance(timestamp, str):
            raise FilingMetadataError("History retrieval timestamp must be UTC")
        history_retrieved = datetime.fromisoformat(timestamp)
        if (
            history_retrieved.tzinfo is None
            or history_retrieved.utcoffset() != timedelta(0)
        ):
            raise FilingMetadataError(
                "History retrieval timestamp must be timezone-aware UTC"
            )
        history_size = item.get("size_bytes")
        history_sha = item.get("sha256")
        if (
            type(history_size) is not int
            or history_size <= 0
            or not isinstance(history_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", history_sha) is None
        ):
            raise FilingMetadataError("History size or SHA-256 is invalid")
        history_content = history_path.read_bytes()
        if (
            len(history_content) != history_size
            or hashlib.sha256(history_content).hexdigest() != history_sha
        ):
            raise FilingMetadataError(
                "History bytes do not match recorded size and SHA-256"
            )
        listing = _parse_history(
            history_content,
            cik,
            reference,
            item["url"],
            history_retrieved.astimezone(UTC),
            history_since=history_since,
        )
        for filing in listing.filings:
            accession = filing.reference.accession_number
            earlier = seen.get(accession)
            if earlier is not None and (
                earlier.form,
                earlier.filing_date,
                earlier.report_date,
                earlier.primary_document,
            ) != (
                filing.form,
                filing.filing_date,
                filing.report_date,
                filing.primary_document,
            ):
                raise FilingMetadataError(
                    f"Conflicting accession across submissions listings: {accession}"
                )
            seen[accession] = filing
        listings.append(listing)
        history_sources[reference.name] = (
            history_path.resolve(strict=True),
            listing,
            history_size,
            history_sha,
        )
    discovery = replace(discovery, history_listings=tuple(listings))
    return _Evidence(
        discovery,
        run_id,
        path.resolve(strict=True),
        submissions.resolve(strict=True),
        size,
        sha256,
        history_sources,
    )


def _ensure_table(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the table, or check the existing columns and primary key match."""
    relation = connection.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_catalog=current_database() AND table_schema='silver' AND table_name='filing_metadata'"
    ).fetchone()
    if relation is None:
        columns = ", ".join(
            f'"{name}" {kind}' + (" NOT NULL" if nullable == "NO" else "")
            for name, kind, nullable in _METADATA_SCHEMA
        )
        connection.execute(
            f"CREATE TABLE silver.filing_metadata ({columns}, PRIMARY KEY (cik, accession_number))"
        )
    else:
        if relation[0] != "BASE TABLE":
            raise FilingMetadataError("silver.filing_metadata must be a table")
        actual = tuple(
            row[:3]
            for row in connection.execute("DESCRIBE silver.filing_metadata").fetchall()
        )
        primary_keys = connection.execute(
            "SELECT constraint_column_names FROM duckdb_constraints() "
            "WHERE database_name=current_database() AND schema_name='silver' "
            "AND table_name='filing_metadata' AND constraint_type='PRIMARY KEY'"
        ).fetchall()
        if actual != _METADATA_SCHEMA or primary_keys != [
            (["cik", "accession_number"],)
        ]:
            raise FilingMetadataError(
                "Incompatible silver.filing_metadata schema or primary key"
            )


def _insert_metadata(
    connection: duckdb.DuckDBPyConnection, row: tuple[Any, ...]
) -> None:
    """Insert a new row with its first source/run details; matching rows are handled by _load."""
    connection.execute(
        "INSERT INTO silver.filing_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        row,
    )


def _load(
    connection: duckdb.DuckDBPyConnection, evidence: _Evidence, database: Path
) -> FilingMetadataLoadResult:
    """Match all active company filings to the saved listing without replacing their source details."""
    cik = evidence.discovery.cik
    # The shared catalog may include older active filings beyond this run's selected limit.
    active = connection.execute(
        "SELECT accession_number FROM silver.active_filings WHERE cik=? ORDER BY accession_number",
        [cik],
    ).fetchall()
    _ensure_table(connection)
    filings = {
        filing.reference.accession_number: filing
        for filing in evidence.discovery.all_filings
    }
    inserted = existing = missing = conflicts = 0
    issues: list[FilingMetadataIssue] = []
    for (accession,) in active:
        filing = filings.get(accession)
        if filing is None:
            missing += 1
            issues.append(
                FilingMetadataIssue(
                    cik,
                    accession,
                    "MISSING_METADATA",
                    "Active filing is absent from the verified submissions snapshot",
                )
            )
            continue
        # A matching rerun keeps the source and run that first loaded this row.
        business = tuple(getattr(filing, name) for name in _BUSINESS_FIELDS)
        stored = connection.execute(
            "SELECT form, filing_date, report_date, primary_document FROM silver.filing_metadata WHERE cik=? AND accession_number=?",
            [cik, accession],
        ).fetchone()
        if stored is not None:
            different = [
                name
                for name, old, new in zip(
                    _BUSINESS_FIELDS, stored, business, strict=True
                )
                if old != new
            ]
            if different:
                conflicts += 1
                issues.append(
                    FilingMetadataIssue(
                        cik,
                        accession,
                        "METADATA_CONFLICT",
                        "Conflicting fields: " + ", ".join(different),
                    )
                )
            else:
                existing += 1
            continue
        if filing.source_listing_name is None:
            source_path = evidence.submissions_path
            source_url = evidence.discovery.submissions_url
            source_retrieved = evidence.discovery.retrieved_at
            source_size = evidence.size_bytes
            source_sha = evidence.sha256
        else:
            source_path, source_listing, source_size, source_sha = (
                evidence.history_sources[filing.source_listing_name]
            )
            source_url = source_listing.url
            source_retrieved = source_listing.retrieved_at
        _insert_metadata(
            connection,
            (
                cik,
                accession,
                *business,
                evidence.run_id,
                str(evidence.run_path),
                str(source_path),
                source_url,
                source_retrieved,
                source_size,
                source_sha,
            ),
        )
        inserted += 1
    return FilingMetadataLoadResult(
        "PARTIAL" if issues else "COMPLETE",
        database,
        cik,
        evidence.run_id,
        len(active),
        inserted,
        existing,
        missing,
        conflicts,
        tuple(issues),
    )


def load_filing_metadata(
    *, run_record_path: Path, database_path: Path
) -> FilingMetadataLoadResult:
    """Load dates and forms for active filings from a verified company response.

    The database must exist. Matching rows keep the source and run that first
    loaded them. Missing or conflicting fields become issues; database errors
    roll back the load. All active company filings are checked, not just the
    filings selected by this run's limit.
    """
    if not isinstance(run_record_path, Path) or not isinstance(database_path, Path):
        raise FilingMetadataError("Run record and database must be pathlib.Path values")
    try:
        # Check saved source evidence before opening a write connection to the database.
        evidence = _read_evidence(run_record_path)
    except (OSError, TypeError, ValueError) as exc:
        raise FilingMetadataError(f"Invalid filing metadata evidence: {exc}") from exc
    try:
        database = database_path.resolve(strict=True)
        if not database.is_file():
            raise FilingMetadataError("Database must be an existing file")
        with duckdb.connect(str(database)) as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                result = _load(connection, evidence, database)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return result
    except (OSError, duckdb.Error) as exc:
        raise FilingMetadataError(
            f"Filing metadata database operation failed: {exc}"
        ) from exc
