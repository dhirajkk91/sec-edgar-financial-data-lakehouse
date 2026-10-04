"""Persist verified fiscal extraction results in an existing local catalog."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

import duckdb

from sec_edgar_lakehouse.filing_fiscal_metadata import (
    FilingFiscalMetadata,
    FiscalMetadataExtractionError,
    FiscalMetadataInputError,
    extract_filing_fiscal_metadata,
)

_SCHEMA = (
    ("cik", "VARCHAR", "NO"),
    ("accession_number", "VARCHAR", "NO"),
    ("source_document_name", "VARCHAR", "NO"),
    ("source_sha256", "VARCHAR", "NO"),
    ("extraction_version", "VARCHAR", "NO"),
    ("form", "VARCHAR", "NO"),
    ("report_date", "DATE", "NO"),
    ("metadata_run_id", "VARCHAR", "NO"),
    ("submissions_sha256", "VARCHAR", "NO"),
    ("extraction_status", "VARCHAR", "NO"),
    ("fiscal_year_focus", "INTEGER", "YES"),
    ("fiscal_period_focus", "VARCHAR", "YES"),
    ("document_period_end_date", "DATE", "YES"),
    ("occurrences_json", "VARCHAR", "NO"),
    ("issues_json", "VARCHAR", "NO"),
)
_KEY = tuple(name for name, _, _ in _SCHEMA[:5])
_COLUMNS = ", ".join(name for name, _, _ in _SCHEMA)
_KEY_WHERE = " AND ".join(f"{name}=?" for name in _KEY)


class FiscalMetadataLoadError(Exception):
    """Verification, schema or persistence prevents a fiscal metadata load."""


@dataclass(frozen=True, slots=True)
class FilingFiscalMetadataLoadResult:
    outcome: Literal["INSERTED", "ALREADY_EXISTS"]
    database_path: Path
    metadata: FilingFiscalMetadata

    @property
    def status(self) -> Literal["COMPLETE", "PARTIAL"]:
        return self.metadata.status


def _row(metadata: FilingFiscalMetadata) -> tuple[Any, ...]:
    occurrences = []
    for occurrence in metadata.occurrences:
        record = asdict(occurrence)
        record["period_start"] = (
            occurrence.period_start.isoformat() if occurrence.period_start else None
        )
        record["period_end"] = (
            occurrence.period_end.isoformat() if occurrence.period_end else None
        )
        occurrences.append(record)
    issues = [asdict(issue) for issue in metadata.issues]
    return (
        metadata.reference.cik,
        metadata.reference.accession_number,
        metadata.selected_document_name,
        metadata.selected_document_sha256,
        metadata.extraction_version,
        metadata.form,
        metadata.report_date,
        metadata.metadata_run_id,
        metadata.submissions_sha256,
        metadata.status,
        metadata.fiscal_year_focus,
        metadata.fiscal_period_focus,
        metadata.document_period_end_date,
        json.dumps(
            occurrences,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        json.dumps(
            issues,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _recheck(
    connection: duckdb.DuckDBPyConnection, metadata: FilingFiscalMetadata
) -> None:
    identity = [metadata.reference.cik, metadata.reference.accession_number]
    active = connection.execute(
        "SELECT source_document_name, source_sha256 FROM silver.active_filings WHERE cik=? AND accession_number=?",
        identity,
    ).fetchall()
    if active != [(metadata.selected_document_name, metadata.selected_document_sha256)]:
        raise FiscalMetadataLoadError(
            "Active filing source changed or is no longer unique"
        )
    catalog = connection.execute(
        "SELECT form, report_date, metadata_run_id, submissions_sha256 FROM silver.filing_metadata WHERE cik=? AND accession_number=?",
        identity,
    ).fetchall()
    if catalog != [
        (
            metadata.form,
            metadata.report_date,
            metadata.metadata_run_id,
            metadata.submissions_sha256,
        )
    ]:
        raise FiscalMetadataLoadError("Filing metadata changed or is no longer unique")


def _ensure_table(connection: duckdb.DuckDBPyConnection) -> None:
    relation = connection.execute(
        "SELECT table_type FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema='silver' AND table_name='filing_fiscal_metadata'"
    ).fetchone()
    if relation is None:
        columns = ", ".join(
            name + " " + kind + (" NOT NULL" if nullable == "NO" else "")
            for name, kind, nullable in _SCHEMA
        )
        connection.execute(
            "CREATE TABLE silver.filing_fiscal_metadata ("
            + columns
            + ", PRIMARY KEY ("
            + ", ".join(_KEY)
            + "))"
        )
        return
    schema = tuple(
        row[:3]
        for row in connection.execute(
            "DESCRIBE silver.filing_fiscal_metadata"
        ).fetchall()
    )
    keys = connection.execute(
        "SELECT constraint_column_names FROM duckdb_constraints() WHERE database_name=current_database() AND schema_name='silver' AND table_name='filing_fiscal_metadata' AND constraint_type='PRIMARY KEY'"
    ).fetchall()
    if relation[0] != "BASE TABLE" or schema != _SCHEMA or keys != [(list(_KEY),)]:
        raise FiscalMetadataLoadError(
            "Incompatible silver.filing_fiscal_metadata relation, ordered schema or primary key"
        )


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError(f"Non-JSON constant {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key}")
        result[key] = value
    return result


def _evidence_array(value: str, field: str) -> str:
    try:
        array = json.loads(
            value, parse_constant=_invalid_constant, object_pairs_hook=_unique_object
        )
        if not isinstance(array, list):
            raise TypeError("Evidence must be a JSON array")
        # Object formatting is irrelevant; array order and typed contents are significant.
        return json.dumps(
            array,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (ValueError, TypeError) as exc:
        raise FiscalMetadataLoadError(f"Malformed stored {field}: {exc}") from exc


def _verify_row(stored: tuple[Any, ...] | None, expected: tuple[Any, ...]) -> None:
    if stored is None:
        raise FiscalMetadataLoadError("Fiscal metadata read-back found no stored row")
    conflicts = [
        name
        for (name, _, _), old, new in zip(
            _SCHEMA[:13], stored[:13], expected[:13], strict=True
        )
        if old != new
    ]
    for index in (13, 14):
        field = _SCHEMA[index][0]
        if _evidence_array(stored[index], field) != _evidence_array(
            expected[index], field
        ):
            conflicts.append(field)
    if conflicts:
        raise FiscalMetadataLoadError(
            "Conflicting fiscal metadata fields: " + ", ".join(conflicts)
        )


def _insert_row(connection: duckdb.DuckDBPyConnection, row: tuple[Any, ...]) -> None:
    connection.execute(
        "INSERT INTO silver.filing_fiscal_metadata ("
        + _COLUMNS
        + ") VALUES ("
        + ", ".join("?" for _ in row)
        + ")",
        row,
    )


def load_filing_fiscal_metadata(
    filing_directory: Path, manifest_path: Path, *, database_path: Path
) -> FilingFiscalMetadataLoadResult:
    """Extract once, then transactionally insert or verify identical persisted evidence."""
    try:
        metadata = extract_filing_fiscal_metadata(
            filing_directory, manifest_path, database_path=database_path
        )
    except (FiscalMetadataInputError, FiscalMetadataExtractionError) as exc:
        raise FiscalMetadataLoadError(f"Fiscal extraction failed: {exc}") from exc
    if metadata.extraction_version != "1":
        raise FiscalMetadataLoadError(
            "Storage supports fiscal extraction version 1 only"
        )
    row = _row(metadata)
    try:
        database = database_path.resolve(strict=True)
        if not database.is_file():
            raise FiscalMetadataLoadError("Database must be an existing file")
        with duckdb.connect(str(database)) as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                _recheck(connection, metadata)
                _ensure_table(connection)
                query = (
                    "SELECT "
                    + _COLUMNS
                    + " FROM silver.filing_fiscal_metadata WHERE "
                    + _KEY_WHERE
                )
                stored = connection.execute(query, row[:5]).fetchone()
                outcome: Literal["INSERTED", "ALREADY_EXISTS"] = "ALREADY_EXISTS"
                if stored is None:
                    _insert_row(connection, row)
                    outcome = "INSERTED"
                else:
                    _verify_row(stored, row)
                _verify_row(connection.execute(query, row[:5]).fetchone(), row)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return FilingFiscalMetadataLoadResult(outcome, database, metadata)
    except (OSError, duckdb.Error) as exc:
        raise FiscalMetadataLoadError(
            f"Fiscal metadata database operation failed: {exc}"
        ) from exc
