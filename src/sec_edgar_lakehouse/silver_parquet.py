"""Write one in-memory Silver extraction to verified local Parquet files."""

import json
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb

from sec_edgar_lakehouse.silver_models import SilverExtraction

PARSER_VERSION = "1"
SCHEMA_VERSION = "1"

_FACTS_SCHEMA = (
    ("source_occurrence_id", "VARCHAR"),
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("source_document_name", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
    ("source_ordinal", "BIGINT"),
    ("processing_run_id", "VARCHAR"),
    ("parser_version", "VARCHAR"),
    ("schema_version", "VARCHAR"),
    ("concept_namespace", "VARCHAR"),
    ("concept_local_name", "VARCHAR"),
    ("raw_value", "VARCHAR"),
    ("value_decimal", "DECIMAL(38,18)"),
    ("is_nil", "BOOLEAN"),
    ("decimals", "VARCHAR"),
    ("precision", "VARCHAR"),
    ("unit_ref", "VARCHAR"),
    ("unit_expression", "VARCHAR"),
    ("unit_xml", "VARCHAR"),
    ("context_ref", "VARCHAR"),
    ("context_xml", "VARCHAR"),
    ("entity_scheme", "VARCHAR"),
    ("entity_identifier", "VARCHAR"),
    ("period_kind", "VARCHAR"),
    ("period_start", "DATE"),
    ("period_end", "DATE"),
    ("period_instant", "DATE"),
)

_DIMENSIONS_SCHEMA = (
    ("source_occurrence_id", "VARCHAR"),
    ("axis_namespace", "VARCHAR"),
    ("axis_name", "VARCHAR"),
    ("member_kind", "VARCHAR"),
    ("member_value", "VARCHAR"),
    ("typed_member_xml", "VARCHAR"),
    ("context_location", "VARCHAR"),
)

_REJECTIONS_SCHEMA = (
    ("source_occurrence_id", "VARCHAR"),
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("source_document_name", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
    ("source_ordinal", "BIGINT"),
    ("processing_run_id", "VARCHAR"),
    ("parser_version", "VARCHAR"),
    ("schema_version", "VARCHAR"),
    ("concept_namespace", "VARCHAR"),
    ("concept_local_name", "VARCHAR"),
    ("raw_value", "VARCHAR"),
    ("context_ref", "VARCHAR"),
    ("unit_ref", "VARCHAR"),
    ("decimals", "VARCHAR"),
    ("precision", "VARCHAR"),
    ("raw_attributes_json", "VARCHAR"),
    ("reason_code", "VARCHAR"),
    ("reason", "VARCHAR"),
)


class SilverWriteError(Exception):
    """Silver rows could not be written and verified safely."""


@dataclass(frozen=True, slots=True)
class SilverParquetFiles:
    """Paths and row counts for one local Silver Parquet output."""

    facts_path: Path
    fact_dimensions_path: Path
    rejected_facts_path: Path
    facts_row_count: int
    fact_dimensions_row_count: int
    rejected_facts_row_count: int


def write_silver_parquet(
    extraction: SilverExtraction,
    output_directory: Path,
    *,
    processing_run_id: str,
) -> SilverParquetFiles:
    """Write and verify three Parquet files in a new local directory."""
    _validate_inputs(extraction, output_directory, processing_run_id)
    if output_directory.exists() or output_directory.is_symlink():
        raise SilverWriteError(f"Output path already exists: {output_directory}")

    output_directory = output_directory.resolve(strict=False)
    facts_path = output_directory / "facts.parquet"
    dimensions_path = output_directory / "fact_dimensions.parquet"
    rejections_path = output_directory / "rejected_facts.parquet"
    created = False
    try:
        output_directory.mkdir()
        created = True
        _write_files(
            extraction,
            processing_run_id,
            facts_path,
            dimensions_path,
            rejections_path,
        )
        _verify_files(
            extraction,
            facts_path,
            dimensions_path,
            rejections_path,
        )
    except Exception as exc:
        cleanup_error: OSError | None = None
        if created:
            try:
                shutil.rmtree(output_directory)
            except OSError as error:
                cleanup_error = error
        if cleanup_error is not None:
            raise SilverWriteError(
                f"Could not write Silver Parquet files: {exc}; "
                f"cleanup failed: {cleanup_error}"
            ) from exc
        if isinstance(exc, SilverWriteError):
            raise
        raise SilverWriteError(f"Could not write Silver Parquet files: {exc}") from exc

    return SilverParquetFiles(
        facts_path=facts_path,
        fact_dimensions_path=dimensions_path,
        rejected_facts_path=rejections_path,
        facts_row_count=len(extraction.accepted_facts),
        fact_dimensions_row_count=len(extraction.dimensions),
        rejected_facts_row_count=len(extraction.rejected_occurrences),
    )


def _validate_inputs(
    extraction: SilverExtraction,
    output_directory: Path,
    processing_run_id: str,
) -> None:
    if not isinstance(extraction, SilverExtraction):
        raise SilverWriteError("Extraction must be a SilverExtraction")
    if not isinstance(output_directory, Path):
        raise SilverWriteError("Output directory must be a pathlib.Path")
    if not isinstance(processing_run_id, str) or not processing_run_id.strip():
        raise SilverWriteError("Processing run ID must be a non-empty string")
    if not output_directory.parent.exists():
        raise SilverWriteError(
            f"Output directory parent does not exist: {output_directory.parent}"
        )
    if not output_directory.parent.is_dir():
        raise SilverWriteError(
            f"Output directory parent is not a directory: {output_directory.parent}"
        )
    if extraction.accepted_count != len(extraction.accepted_facts):
        raise SilverWriteError("Accepted fact count does not match the extraction")
    if extraction.rejected_count != len(extraction.rejected_occurrences):
        raise SilverWriteError("Rejected fact count does not match the extraction")
    if (
        extraction.candidate_count
        != extraction.accepted_count + extraction.rejected_count
    ):
        raise SilverWriteError("Candidate counts do not reconcile")
    for fact in extraction.accepted_facts:
        if (
            fact.cik != extraction.reference.cik
            or fact.accession_number != extraction.reference.accession_number
            or fact.source_document_name != extraction.selected_document_name
            or fact.source_sha256 != extraction.selected_document_sha256
        ):
            raise SilverWriteError(
                "Accepted fact source identity does not match the extraction"
            )
        if fact.is_nil != (fact.value_decimal is None):
            raise SilverWriteError("Fact nil flag and decimal value do not agree")
        if not fact.is_nil and not isinstance(fact.value_decimal, Decimal):
            raise SilverWriteError(
                "Non-nil fact value_decimal must be a decimal.Decimal"
            )


def _write_files(
    extraction: SilverExtraction,
    processing_run_id: str,
    facts_path: Path,
    dimensions_path: Path,
    rejections_path: Path,
) -> None:
    connection = duckdb.connect(database=":memory:")
    try:
        _create_table(connection, "facts", _FACTS_SCHEMA)
        _create_table(connection, "fact_dimensions", _DIMENSIONS_SCHEMA)
        _create_table(connection, "rejected_facts", _REJECTIONS_SCHEMA)
        _insert_rows(
            connection,
            "facts",
            _FACTS_SCHEMA,
            _fact_rows(extraction, processing_run_id),
        )
        _insert_rows(
            connection,
            "fact_dimensions",
            _DIMENSIONS_SCHEMA,
            _dimension_rows(extraction),
        )
        _insert_rows(
            connection,
            "rejected_facts",
            _REJECTIONS_SCHEMA,
            _rejection_rows(extraction, processing_run_id),
        )
        connection.execute("COPY facts TO ? (FORMAT PARQUET)", [str(facts_path)])
        connection.execute(
            "COPY fact_dimensions TO ? (FORMAT PARQUET)", [str(dimensions_path)]
        )
        connection.execute(
            "COPY rejected_facts TO ? (FORMAT PARQUET)", [str(rejections_path)]
        )
    finally:
        connection.close()


def _create_table(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    schema: tuple[tuple[str, str], ...],
) -> None:
    columns = ", ".join(f'"{name}" {data_type}' for name, data_type in schema)
    connection.execute(f'CREATE TABLE "{table_name}" ({columns})')


def _insert_rows(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    schema: tuple[tuple[str, str], ...],
    rows: list[tuple[object, ...]],
) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" for _ in schema)
    connection.executemany(f'INSERT INTO "{table_name}" VALUES ({placeholders})', rows)


def _fact_rows(
    extraction: SilverExtraction, processing_run_id: str
) -> list[tuple[object, ...]]:
    return [
        (
            fact.source_occurrence_id,
            fact.cik,
            fact.accession_number,
            fact.source_document_name,
            fact.source_sha256,
            fact.source_ordinal,
            processing_run_id,
            PARSER_VERSION,
            SCHEMA_VERSION,
            fact.concept_namespace,
            fact.concept_local_name,
            fact.raw_value,
            fact.value_decimal,
            fact.is_nil,
            fact.decimals,
            fact.precision,
            fact.unit_ref,
            fact.unit_expression,
            fact.unit_xml,
            fact.context_ref,
            fact.context_xml,
            fact.entity_scheme,
            fact.entity_identifier,
            fact.period_kind,
            fact.period_start,
            fact.period_end,
            fact.period_instant,
        )
        for fact in extraction.accepted_facts
    ]


def _dimension_rows(extraction: SilverExtraction) -> list[tuple[object, ...]]:
    return [
        (
            dimension.source_occurrence_id,
            dimension.axis_namespace,
            dimension.axis_name,
            dimension.member_kind,
            dimension.member_value,
            dimension.typed_member_xml,
            dimension.context_location,
        )
        for dimension in extraction.dimensions
    ]


def _rejection_rows(
    extraction: SilverExtraction, processing_run_id: str
) -> list[tuple[object, ...]]:
    return [
        (
            rejection.source_occurrence_id,
            extraction.reference.cik,
            extraction.reference.accession_number,
            extraction.selected_document_name,
            extraction.selected_document_sha256,
            rejection.source_ordinal,
            processing_run_id,
            PARSER_VERSION,
            SCHEMA_VERSION,
            rejection.concept_namespace,
            rejection.concept_local_name,
            rejection.raw_value,
            rejection.context_ref,
            rejection.unit_ref,
            rejection.decimals,
            rejection.precision,
            # An array of pairs preserves every attribute without collapsing names.
            json.dumps(
                rejection.raw_attributes,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            rejection.reason_code,
            rejection.reason,
        )
        for rejection in extraction.rejected_occurrences
    ]


def _verify_files(
    extraction: SilverExtraction,
    facts_path: Path,
    dimensions_path: Path,
    rejections_path: Path,
) -> None:
    connection = duckdb.connect(database=":memory:")
    try:
        _verify_schema(connection, facts_path, _FACTS_SCHEMA)
        _verify_schema(connection, dimensions_path, _DIMENSIONS_SCHEMA)
        _verify_schema(connection, rejections_path, _REJECTIONS_SCHEMA)
        _verify_count(connection, facts_path, len(extraction.accepted_facts), "facts")
        _verify_count(
            connection,
            dimensions_path,
            len(extraction.dimensions),
            "fact dimensions",
        )
        _verify_count(
            connection,
            rejections_path,
            len(extraction.rejected_occurrences),
            "rejected facts",
        )
        _verify_fact_identity(connection, extraction, facts_path)
        _verify_dimension_links(connection, facts_path, dimensions_path)
        _verify_decimals(connection, extraction, facts_path)
        _verify_rejections(connection, extraction, rejections_path)
    finally:
        connection.close()


def _verify_schema(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    expected: tuple[tuple[str, str], ...],
) -> None:
    rows = connection.execute(
        "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
    ).fetchall()
    actual = tuple((str(row[0]), str(row[1])) for row in rows)
    if actual != expected:
        raise SilverWriteError(
            f"Parquet schema mismatch for {path.name}: expected {expected!r}, "
            f"found {actual!r}"
        )


def _verify_count(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    expected: int,
    subject: str,
) -> None:
    row = connection.execute(
        "SELECT count(*) FROM read_parquet(?)", [str(path)]
    ).fetchone()
    if row is None:
        raise SilverWriteError(f"Could not read Parquet {subject} count")
    actual = int(row[0])
    if actual != expected:
        raise SilverWriteError(
            f"Parquet {subject} count mismatch: expected {expected}, found {actual}"
        )


def _verify_fact_identity(
    connection: duckdb.DuckDBPyConnection,
    extraction: SilverExtraction,
    facts_path: Path,
) -> None:
    rows = connection.execute(
        "SELECT source_occurrence_id FROM read_parquet(?)", [str(facts_path)]
    ).fetchall()
    actual_ids = tuple(str(row[0]) for row in rows)
    expected_ids = tuple(
        fact.source_occurrence_id for fact in extraction.accepted_facts
    )
    if actual_ids != expected_ids:
        raise SilverWriteError(
            "Fact occurrence order or identity changed during writing"
        )
    if len(actual_ids) != len(set(actual_ids)):
        raise SilverWriteError("Fact source_occurrence_id values must be unique")


def _verify_dimension_links(
    connection: duckdb.DuckDBPyConnection,
    facts_path: Path,
    dimensions_path: Path,
) -> None:
    row = connection.execute(
        """
        SELECT count(*)
        FROM read_parquet(?) AS dimensions
        LEFT JOIN read_parquet(?) AS facts USING (source_occurrence_id)
        WHERE facts.source_occurrence_id IS NULL
        """,
        [str(dimensions_path), str(facts_path)],
    ).fetchone()
    if row is None:
        raise SilverWriteError("Could not verify dimension links")
    missing = int(row[0])
    if missing:
        raise SilverWriteError(
            f"{missing} dimension rows do not refer to an accepted fact"
        )


def _verify_decimals(
    connection: duckdb.DuckDBPyConnection,
    extraction: SilverExtraction,
    facts_path: Path,
) -> None:
    rows = connection.execute(
        "SELECT source_occurrence_id, value_decimal, is_nil FROM read_parquet(?)",
        [str(facts_path)],
    ).fetchall()
    expected = {
        fact.source_occurrence_id: (fact.value_decimal, fact.is_nil)
        for fact in extraction.accepted_facts
    }
    for source_id, value, is_nil in rows:
        expected_value, expected_nil = expected[str(source_id)]
        if is_nil is not expected_nil:
            raise SilverWriteError(
                f"Fact {source_id} nil flag changed during Parquet round-trip"
            )
        if expected_nil:
            if value is not None:
                raise SilverWriteError(
                    f"Nil fact {source_id} gained a numeric value during writing"
                )
        elif not isinstance(value, Decimal) or value != expected_value:
            raise SilverWriteError(
                f"Fact {source_id} decimal changed during Parquet round-trip"
            )


def _verify_rejections(
    connection: duckdb.DuckDBPyConnection,
    extraction: SilverExtraction,
    rejections_path: Path,
) -> None:
    rows = connection.execute(
        "SELECT source_occurrence_id, raw_attributes_json FROM read_parquet(?)",
        [str(rejections_path)],
    ).fetchall()
    actual_ids = tuple(str(row[0]) for row in rows)
    expected_ids = tuple(
        rejection.source_occurrence_id for rejection in extraction.rejected_occurrences
    )
    if actual_ids != expected_ids:
        raise SilverWriteError(
            "Rejected occurrence order or identity changed during writing"
        )
    for row, rejection in zip(rows, extraction.rejected_occurrences, strict=True):
        try:
            attributes = json.loads(str(row[1]))
        except json.JSONDecodeError as exc:
            raise SilverWriteError(
                f"Rejected fact {row[0]} has invalid raw attributes JSON"
            ) from exc
        expected_attributes = [list(item) for item in rejection.raw_attributes]
        if attributes != expected_attributes:
            raise SilverWriteError(
                f"Rejected fact {row[0]} raw attributes changed during writing"
            )
