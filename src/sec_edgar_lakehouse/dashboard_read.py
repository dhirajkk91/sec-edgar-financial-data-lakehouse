"""Materialize the three Gold marts through a dedicated read-only connection."""

import stat
from dataclasses import dataclass
from pathlib import Path

import duckdb

from sec_edgar_lakehouse.filing_reference import FilingReference, _normalize_cik

# Ordered projections from the Gold models and transform/models/properties.yml.
_FILINGS_SCHEMA = (
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("form", "VARCHAR"),
    ("filing_date", "DATE"),
    ("report_date", "DATE"),
    ("primary_document", "VARCHAR"),
    ("is_amendment", "BOOLEAN"),
    ("fiscal_year_focus", "INTEGER"),
    ("fiscal_period_focus", "VARCHAR"),
    ("document_period_end_date", "DATE"),
    ("fiscal_metadata_status", "VARCHAR"),
    ("fiscal_extraction_version", "VARCHAR"),
    ("metadata_run_id", "VARCHAR"),
    ("submissions_sha256", "VARCHAR"),
    ("source_document_name", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
    ("silver_status", "VARCHAR"),
    ("parser_version", "VARCHAR"),
    ("schema_version", "VARCHAR"),
    ("activation_run_id", "VARCHAR"),
    ("activated_at", "TIMESTAMPTZ"),
    ("version_path", "VARCHAR"),
    ("publication_path", "VARCHAR"),
    ("publication_sha256", "VARCHAR"),
    ("accepted_fact_count", "BIGINT"),
    ("dimension_count", "BIGINT"),
    ("rejected_fact_count", "BIGINT"),
    ("candidate_fact_count", "BIGINT"),
    ("financial_metric_row_count", "BIGINT"),
    ("reported_metric_row_count", "BIGINT"),
    ("derived_metric_row_count", "BIGINT"),
    ("distinct_metric_count", "BIGINT"),
    ("present_critical_metric_count", "BIGINT"),
    ("missing_critical_metric_codes", "VARCHAR[]"),
    ("quality_issue_count", "BIGINT"),
    ("error_issue_count", "BIGINT"),
    ("warning_issue_count", "BIGINT"),
    ("gold_quality_status", "VARCHAR"),
    ("gold_quality_reason", "VARCHAR"),
    ("quality_rule_version", "VARCHAR"),
)

_FINANCIAL_SCHEMA = (
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("metric_code", "VARCHAR"),
    ("form", "VARCHAR"),
    ("filing_date", "DATE"),
    ("report_date", "DATE"),
    ("fiscal_year_focus", "INTEGER"),
    ("fiscal_period_focus", "VARCHAR"),
    ("period_kind", "VARCHAR"),
    ("period_start", "DATE"),
    ("period_end", "DATE"),
    ("period_instant", "DATE"),
    ("period_label", "VARCHAR"),
    ("value_origin", "VARCHAR"),
    ("value_decimal", "DECIMAL(38,18)"),
    ("unit_expression", "VARCHAR"),
    ("selection_rule_version", "VARCHAR"),
    ("period_rule_version", "VARCHAR"),
    ("derivation_rule_version", "VARCHAR"),
    ("derivation_method", "VARCHAR"),
    ("concept_namespace", "VARCHAR"),
    ("concept_local_name", "VARCHAR"),
    ("raw_value", "VARCHAR"),
    ("current_source_occurrence_id", "VARCHAR"),
    ("current_source_document_name", "VARCHAR"),
    ("current_source_sha256", "VARCHAR"),
    ("current_value_decimal", "DECIMAL(38,18)"),
    ("current_supporting_occurrence_ids", "VARCHAR[]"),
    ("previous_accession_number", "VARCHAR"),
    ("previous_source_occurrence_id", "VARCHAR"),
    ("previous_source_document_name", "VARCHAR"),
    ("previous_source_sha256", "VARCHAR"),
    ("previous_value_decimal", "DECIMAL(38,18)"),
    ("previous_supporting_occurrence_ids", "VARCHAR[]"),
    ("cumulative_period_start", "DATE"),
    ("previous_period_end", "DATE"),
    ("current_decimals", "VARCHAR"),
    ("current_precision", "VARCHAR"),
    ("previous_decimals", "VARCHAR"),
    ("previous_precision", "VARCHAR"),
)

_ISSUES_SCHEMA = (
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("metric_code", "VARCHAR"),
    ("issue_stage", "VARCHAR"),
    ("source_model", "VARCHAR"),
    ("issue_code", "VARCHAR"),
    ("severity", "VARCHAR"),
    ("report_date", "DATE"),
    ("period_start", "DATE"),
    ("period_end", "DATE"),
    ("period_instant", "DATE"),
    ("fiscal_year_focus", "INTEGER"),
    ("fiscal_period_focus", "VARCHAR"),
    ("current_source_occurrence_id", "VARCHAR"),
    ("source_occurrence_ids", "VARCHAR[]"),
    ("previous_source_occurrence_ids", "VARCHAR[]"),
    ("details", "VARCHAR"),
)


class DashboardReadError(Exception):
    """Invalid input, unavailable Gold schema, or an expected read failure."""


@dataclass(frozen=True, slots=True)
class DashboardTable:
    """An ordered, typed table with immutable evidence arrays."""

    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    """The selected Gold records, materialized before the connection closes."""

    database_path: Path
    available_ciks: tuple[str, ...]
    cik: str | None
    accession_number: str | None
    filings: DashboardTable
    financial_metrics: DashboardTable
    quality_issues: DashboardTable


def _immutable(value: object) -> object:
    if isinstance(value, list):
        return tuple(_immutable(item) for item in value)
    return value


def _canonical_type(kind: str) -> str:
    return "TIMESTAMP WITH TIME ZONE" if kind == "TIMESTAMPTZ" else kind


def _read_table(
    connection: duckdb.DuckDBPyConnection,
    relation: str,
    schema: tuple[tuple[str, str], ...],
    where: str,
    parameters: list[str],
    order: str,
) -> DashboardTable:
    projection = ", ".join(name for name, _ in schema)
    cursor = connection.execute(
        f"SELECT {projection} FROM {relation}{where} ORDER BY {order}", parameters
    )
    columns = tuple(column[0] for column in cursor.description)
    types = tuple(str(column[1]) for column in cursor.description)
    actual = tuple(zip(columns, map(_canonical_type, types)))
    expected = tuple((name, _canonical_type(kind)) for name, kind in schema)
    if actual != expected:
        raise DashboardReadError(
            f"Incompatible projected schema for {relation}: expected {expected}, got {actual}"
        )
    return DashboardTable(
        columns,
        types,
        tuple(tuple(_immutable(value) for value in row) for row in cursor.fetchall()),
    )


def load_dashboard_snapshot(
    *,
    database_path: Path,
    cik: str | None = None,
    accession_number: str | None = None,
) -> DashboardSnapshot:
    """Read existing Gold records without modifying or interpreting their results."""
    if not isinstance(database_path, Path):
        raise DashboardReadError("database_path must be a pathlib.Path")
    try:
        normalized_cik = _normalize_cik(cik) if cik is not None else None
        if accession_number is not None:
            if normalized_cik is None:
                raise DashboardReadError("An accession filter requires a CIK")
            FilingReference(normalized_cik, accession_number)
    except (TypeError, ValueError) as exc:
        raise DashboardReadError(f"Invalid dashboard filter: {exc}") from exc
    try:
        database = database_path.resolve(strict=True)
        if not stat.S_ISREG(database.stat().st_mode):
            raise DashboardReadError(
                f"Database must be an existing regular file: {database}"
            )
    except (OSError, ValueError) as exc:
        raise DashboardReadError(
            f"Cannot access database {database_path}: {exc}"
        ) from exc

    where = ""
    parameters = []
    if normalized_cik is not None:
        where = " WHERE cik = ?"
        parameters.append(normalized_cik)
        if accession_number is not None:
            where += " AND accession_number = ?"
            parameters.append(accession_number)
    try:
        connection = duckdb.connect(str(database), read_only=True)
        try:
            connection.execute("BEGIN TRANSACTION")
            filings = _read_table(
                connection,
                "gold.dim_filings",
                _FILINGS_SCHEMA,
                where,
                parameters,
                "cik ASC, filing_date DESC NULLS LAST, accession_number DESC",
            )
            available_ciks = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT cik FROM gold.dim_filings "
                    "WHERE cik IS NOT NULL ORDER BY cik ASC"
                ).fetchall()
            )
            financial_metrics = _read_table(
                connection,
                "gold.fct_financial_metrics",
                _FINANCIAL_SCHEMA,
                where,
                parameters,
                "ALL",
            )
            quality_issues = _read_table(
                connection,
                "gold.metric_quality_issues",
                _ISSUES_SCHEMA,
                where,
                parameters,
                "ALL",
            )
            snapshot = DashboardSnapshot(
                database,
                available_ciks,
                normalized_cik,
                accession_number,
                filings,
                financial_metrics,
                quality_issues,
            )
            connection.execute("COMMIT")
            return snapshot
        finally:
            connection.close()
    except (duckdb.Error, OSError) as exc:
        raise DashboardReadError(
            f"Cannot read dashboard database {database}: {exc}"
        ) from exc
