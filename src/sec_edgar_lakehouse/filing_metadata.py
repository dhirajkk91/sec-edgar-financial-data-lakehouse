"""Load verified submissions metadata for active Silver filings."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb

from sec_edgar_lakehouse.company_filings import (
    CompanyFilingsDiscovery,
    _invalid_constant,
    _object,
    _parse,
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
    cik: str
    accession_number: str
    reason_code: Literal["MISSING_METADATA", "METADATA_CONFLICT"]
    message: str


@dataclass(frozen=True, slots=True)
class FilingMetadataLoadResult:
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
    discovery: CompanyFilingsDiscovery
    run_id: str
    run_path: Path
    submissions_path: Path
    size_bytes: int
    sha256: str


def _read_evidence(path: Path) -> _Evidence:
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
    discovery = _parse(content, cik, url, retrieved.astimezone(UTC))
    return _Evidence(
        discovery,
        run_id,
        path.resolve(strict=True),
        submissions.resolve(strict=True),
        size,
        sha256,
    )


def _ensure_table(connection: duckdb.DuckDBPyConnection) -> None:
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
    connection.execute(
        "INSERT INTO silver.filing_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        row,
    )


def _load(
    connection: duckdb.DuckDBPyConnection, evidence: _Evidence, database: Path
) -> FilingMetadataLoadResult:
    cik = evidence.discovery.cik
    active = connection.execute(
        "SELECT accession_number FROM silver.active_filings WHERE cik=? ORDER BY accession_number",
        [cik],
    ).fetchall()
    _ensure_table(connection)
    filings = {
        filing.reference.accession_number: filing
        for filing in evidence.discovery.recent_filings
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
        _insert_metadata(
            connection,
            (
                cik,
                accession,
                *business,
                evidence.run_id,
                str(evidence.run_path),
                str(evidence.submissions_path),
                evidence.discovery.submissions_url,
                evidence.discovery.retrieved_at,
                evidence.size_bytes,
                evidence.sha256,
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
    """Verify one run's discovery evidence and load matching active filing metadata."""
    if not isinstance(run_record_path, Path) or not isinstance(database_path, Path):
        raise FilingMetadataError("Run record and database must be pathlib.Path values")
    try:
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
