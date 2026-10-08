"""Independent Gold fixtures for the dashboard boundary; no financial policy tests."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import duckdb
import pytest

from sec_edgar_lakehouse import (
    DashboardReadError,
    DashboardSnapshot,
    DashboardTable,
    dashboard_read,
    load_dashboard_snapshot,
)

# Approved schemas declared independently from the Gold properties, not imported
# from the reader. These fixtures remain an oracle for every ordered column/type.
FILINGS_SCHEMA = (
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

FINANCIAL_SCHEMA = (
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

ISSUES_SCHEMA = (
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

SCHEMAS = {
    "dim_filings": FILINGS_SCHEMA,
    "fct_financial_metrics": FINANCIAL_SCHEMA,
    "metric_quality_issues": ISSUES_SCHEMA,
}
CIK_A = "0000000012"
CIK_B = "0000000034"
ACCESSION_A = "0000000012-25-000002"
ACCESSION_B = "0000999999-25-000010"
EXACT = Decimal("12345678901234567890.123456789012345678")


def create_database(path: Path, *, reverse_columns: bool = False) -> Path:
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE SCHEMA gold")
        for name, schema in SCHEMAS.items():
            declaration = reversed(schema) if reverse_columns else schema
            fields = ", ".join(f"{column} {kind}" for column, kind in declaration)
            connection.execute(f"CREATE TABLE gold.{name} ({fields})")
    return path


def insert_record(path: Path, table: str, **overrides: object) -> None:
    row: dict[str, object] = {}
    for column, kind in SCHEMAS[table]:
        row[column] = {
            "VARCHAR": column,
            "DATE": date(2025, 6, 30),
            "TIMESTAMPTZ": datetime(2025, 7, 1, 12, tzinfo=UTC),
            "INTEGER": 2025,
            "BIGINT": 2,
            "BOOLEAN": False,
            "VARCHAR[]": ["second", "first", "second"],
            "DECIMAL(38,18)": EXACT,
        }[kind]
    row.update(cik=CIK_A, accession_number=ACCESSION_A)
    row.update(overrides)
    columns = tuple(column for column, _ in SCHEMAS[table])
    assert set(row) == set(columns)
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            f"INSERT INTO gold.{table} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [row[column] for column in columns],
        )


def populated_database(path: Path) -> Path:
    create_database(path)
    insert_record(path, "dim_filings", gold_quality_status="PARTIAL")
    insert_record(
        path,
        "dim_filings",
        accession_number="0000000012-25-000001",
        form=None,
        filing_date=None,
        report_date=None,
        activated_at=None,
        is_amendment=None,
        fiscal_year_focus=None,
        fiscal_period_focus=None,
        gold_quality_status="FAILED",
        missing_critical_metric_codes=None,
    )
    insert_record(
        path,
        "dim_filings",
        cik=CIK_B,
        accession_number=ACCESSION_B,
        filing_date=date(2025, 7, 30),
        gold_quality_status="COMPLETE",
    )
    for cik, accession in ((CIK_A, ACCESSION_A), (CIK_B, ACCESSION_B)):
        insert_record(
            path,
            "fct_financial_metrics",
            cik=cik,
            accession_number=accession,
            metric_code="revenue",
            period_label="annual",
            value_origin="reported",
            unit_expression="USD",
            period_instant=None,
            previous_supporting_occurrence_ids=[],
            previous_value_decimal=None,
        )
        insert_record(
            path,
            "metric_quality_issues",
            cik=cik,
            accession_number=accession,
            details="Repeated evidence stays visible",
            source_occurrence_ids=[],
            previous_source_occurrence_ids=None,
        )
    insert_record(
        path,
        "metric_quality_issues",
        details="Repeated evidence stays visible",
        source_occurrence_ids=[],
        previous_source_occurrence_ids=None,
    )
    return path


def immutable(value: object) -> object:
    return tuple(immutable(v) for v in value) if isinstance(value, list) else value


def direct_table(
    path: Path, table: str, cik: str | None = None, accession: str | None = None
) -> DashboardTable:
    schema = SCHEMAS[table]
    projection = ", ".join(column for column, _ in schema)
    where = ""
    parameters = []
    if cik is not None:
        where = " WHERE cik = ?"
        parameters.append(cik)
        if accession is not None:
            where += " AND accession_number = ?"
            parameters.append(accession)
    order = (
        "cik ASC, filing_date DESC NULLS LAST, accession_number DESC"
        if table == "dim_filings"
        else ", ".join(f"{column} ASC NULLS LAST" for column, _ in schema)
    )
    with duckdb.connect(str(path), read_only=True) as connection:
        cursor = connection.execute(
            f"SELECT {projection} FROM gold.{table}{where} ORDER BY {order}", parameters
        )
        return DashboardTable(
            tuple(c[0] for c in cursor.description),
            tuple(str(c[1]) for c in cursor.description),
            tuple(tuple(immutable(v) for v in r) for r in cursor.fetchall()),
        )


@pytest.mark.parametrize(
    "cik,accession",
    [
        (None, None),
        ("12", None),
        ("34", None),
        ("34", ACCESSION_B),
        ("12", ACCESSION_A),
    ],
)
def test_complete_records_and_company_filing_isolation(
    tmp_path: Path, cik: str | None, accession: str | None
) -> None:
    path = populated_database(tmp_path / "dashboard.duckdb")
    before = path.read_bytes()
    files_before = set(tmp_path.rglob("*"))
    snapshot = load_dashboard_snapshot(
        database_path=path, cik=cik, accession_number=accession
    )
    normalized = cik.zfill(10) if cik else None
    assert snapshot.database_path == path.resolve()
    assert snapshot.available_ciks == (CIK_A, CIK_B)
    assert snapshot.cik == normalized and snapshot.accession_number == accession
    assert snapshot.filings == direct_table(path, "dim_filings", normalized, accession)
    assert snapshot.financial_metrics == direct_table(
        path, "fct_financial_metrics", normalized, accession
    )
    assert snapshot.quality_issues == direct_table(
        path, "metric_quality_issues", normalized, accession
    )
    assert path.read_bytes() == before
    assert set(tmp_path.rglob("*")) == files_before


@pytest.mark.parametrize(
    "populated,cik,accession",
    [(False, None, None), (True, "56", None), (True, "12", "0000000012-25-999999")],
)
def test_complete_ordered_schemas_and_typed_empty_results(
    tmp_path: Path, populated: bool, cik: str | None, accession: str | None
) -> None:
    path = tmp_path / "dashboard.duckdb"
    populated_database(path) if populated else create_database(
        path, reverse_columns=True
    )
    snapshot = load_dashboard_snapshot(
        database_path=path, cik=cik, accession_number=accession
    )
    assert snapshot.available_ciks == ((CIK_A, CIK_B) if populated else ())
    for table, schema, count in (
        (snapshot.filings, FILINGS_SCHEMA, 40),
        (snapshot.financial_metrics, FINANCIAL_SCHEMA, 40),
        (snapshot.quality_issues, ISSUES_SCHEMA, 17),
    ):
        assert table.columns == tuple(column for column, _ in schema)
        assert len(table.columns) == count
        assert table.column_types == tuple(
            "TIMESTAMP WITH TIME ZONE" if kind == "TIMESTAMPTZ" else kind
            for _, kind in schema
        )
        assert table.rows == ()


def test_native_values_arrays_statuses_duplicates_and_immutability(
    tmp_path: Path,
) -> None:
    path = populated_database(tmp_path / "dashboard.duckdb")
    snapshot = load_dashboard_snapshot(database_path=path, cik="12")
    filing, missing = [
        dict(zip(snapshot.filings.columns, r)) for r in snapshot.filings.rows
    ]
    assert (
        filing["gold_quality_status"] == "PARTIAL"
        and missing["gold_quality_status"] == "FAILED"
    )
    assert filing["activated_at"] == datetime(2025, 7, 1, 12, tzinfo=UTC)
    assert isinstance(filing["activated_at"], datetime)
    assert isinstance(filing["filing_date"], date)
    assert filing["is_amendment"] is False
    assert type(filing["fiscal_year_focus"]) is int
    assert missing["activated_at"] is None and missing["filing_date"] is None
    assert missing["form"] is None and missing["is_amendment"] is None
    assert filing["missing_critical_metric_codes"] == ("second", "first", "second")
    assert missing["missing_critical_metric_codes"] is None
    financial = dict(
        zip(snapshot.financial_metrics.columns, snapshot.financial_metrics.rows[0])
    )
    assert (
        isinstance(financial["value_decimal"], Decimal)
        and financial["value_decimal"] == EXACT
    )
    assert (
        financial["current_value_decimal"] == EXACT
        and financial["previous_value_decimal"] is None
    )
    assert financial["current_supporting_occurrence_ids"] == (
        "second",
        "first",
        "second",
    )
    assert (
        financial["previous_supporting_occurrence_ids"] == ()
        and financial["period_instant"] is None
    )
    assert (
        len(snapshot.quality_issues.rows) == 2
        and snapshot.quality_issues.rows[0] == snapshot.quality_issues.rows[1]
    )
    issue = dict(zip(snapshot.quality_issues.columns, snapshot.quality_issues.rows[0]))
    assert (
        issue["source_occurrence_ids"] == ()
        and issue["previous_source_occurrence_ids"] is None
    )
    for obj in (
        snapshot,
        snapshot.filings,
        snapshot.financial_metrics,
        snapshot.quality_issues,
    ):
        assert not hasattr(obj, "__dict__")
    for target, attribute, replacement in (
        (snapshot, "cik", CIK_B),
        (snapshot.financial_metrics, "rows", ()),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, replacement)
    with pytest.raises(TypeError):
        cast(list[object], financial["current_supporting_occurrence_ids"])[0] = (
            "changed"
        )


def test_ordering_full_records_and_additional_upstream_columns(tmp_path: Path) -> None:
    path = create_database(tmp_path / "dashboard.duckdb", reverse_columns=True)
    for accession, day in (
        ("0000000012-25-000001", date(2025, 1, 1)),
        (ACCESSION_A, date(2025, 1, 1)),
        ("0000000012-25-000003", None),
    ):
        insert_record(path, "dim_filings", accession_number=accession, filing_date=day)
    for ids in (["z", "a"], ["a", "z"], ["a", "z"]):
        insert_record(
            path, "fct_financial_metrics", current_supporting_occurrence_ids=ids
        )
        insert_record(path, "metric_quality_issues", source_occurrence_ids=ids)
    with duckdb.connect(str(path)) as connection:
        for name in SCHEMAS:
            connection.execute(
                f"ALTER TABLE gold.{name} ADD COLUMN future_column INTEGER"
            )
    snapshot = load_dashboard_snapshot(database_path=path)
    assert [r[1] for r in snapshot.filings.rows] == [
        ACCESSION_A,
        "0000000012-25-000001",
        "0000000012-25-000003",
    ]
    assert snapshot.financial_metrics == direct_table(path, "fct_financial_metrics")
    assert snapshot.quality_issues == direct_table(path, "metric_quality_issues")
    assert (
        len(snapshot.financial_metrics.rows) == len(snapshot.quality_issues.rows) == 3
    )
    assert "future_column" not in snapshot.filings.columns


@pytest.mark.parametrize(
    "table,column,kind",
    [
        ("dim_filings", "activated_at", "TIMESTAMP"),
        ("dim_filings", "accepted_fact_count", "INTEGER"),
        ("fct_financial_metrics", "value_decimal", "DOUBLE"),
        ("fct_financial_metrics", "current_value_decimal", "DECIMAL(38,17)"),
        ("metric_quality_issues", "source_occurrence_ids", "VARCHAR"),
    ],
)
def test_incompatible_required_types(
    tmp_path: Path, table: str, column: str, kind: str
) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            f"ALTER TABLE gold.{table} ALTER COLUMN {column} TYPE {kind}"
        )
    with pytest.raises(DashboardReadError, match="Incompatible projected schema"):
        load_dashboard_snapshot(database_path=path)


@pytest.mark.parametrize("table", list(SCHEMAS))
@pytest.mark.parametrize("missing_relation", [False, True])
def test_missing_required_column_or_mart(
    tmp_path: Path, table: str, missing_relation: bool
) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            f"DROP TABLE gold.{table}"
            if missing_relation
            else f"ALTER TABLE gold.{table} DROP COLUMN cik"
        )
    with pytest.raises(DashboardReadError, match="Cannot read dashboard database"):
        load_dashboard_snapshot(database_path=path)


@pytest.mark.parametrize(
    "cik,accession",
    [
        ("0", None),
        (" 12", None),
        ("１２", None),
        ("1 OR 1=1", None),
        (12, None),
        (None, ACCESSION_B),
        ("12", "bad"),
        ("12", 12),
    ],
)
def test_invalid_filters_are_rejected_before_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cik: object, accession: object
) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid input opened a database")

    monkeypatch.setattr(dashboard_read.duckdb, "connect", forbidden)
    with pytest.raises(DashboardReadError):
        load_dashboard_snapshot(
            database_path=path,
            cik=cast(str | None, cik),
            accession_number=cast(str | None, accession),
        )


@pytest.mark.parametrize("which", ["missing", "directory", "string", "nul"])
def test_invalid_path_creates_nothing_and_never_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    paths: dict[str, object] = {
        "missing": tmp_path / "absent" / "dashboard.duckdb",
        "directory": tmp_path,
        "string": str(tmp_path / "dashboard.duckdb"),
        "nul": Path("bad\0path"),
    }
    before = set(tmp_path.rglob("*"))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid path opened a database")

    monkeypatch.setattr(dashboard_read.duckdb, "connect", forbidden)
    with pytest.raises(DashboardReadError):
        load_dashboard_snapshot(database_path=cast(Path, paths[which]))
    assert set(tmp_path.rglob("*")) == before


def test_canonical_path_and_corrupt_file(tmp_path: Path) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")
    alias = tmp_path / "alias.duckdb"
    alias.symlink_to(path)
    assert load_dashboard_snapshot(database_path=alias).database_path == path.resolve()
    corrupt = tmp_path / "corrupt.duckdb"
    corrupt.write_bytes(b"not a database")
    before = corrupt.read_bytes()
    with pytest.raises(DashboardReadError):
        load_dashboard_snapshot(database_path=corrupt)
    assert corrupt.read_bytes() == before


class TrackedConnection:
    def __init__(
        self, connection: duckdb.DuckDBPyConnection, error: Exception | None = None
    ):
        self.connection = connection
        self.error = error
        self.closed = False
        self.queries: list[tuple[str, object]] = []

    def execute(self, sql: str, parameters: object = None) -> duckdb.DuckDBPyConnection:
        self.queries.append((sql, parameters))
        if self.error and "gold.metric_quality_issues" in sql:
            raise self.error
        return self.connection.execute(sql, parameters)

    def close(self) -> None:
        self.closed = True
        self.connection.close()


@pytest.mark.parametrize(
    "error",
    [
        None,
        duckdb.IOException("Referenced source unavailable"),
        RuntimeError("programming defect"),
    ],
)
def test_transaction_bound_filters_and_connection_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception | None
) -> None:
    path = populated_database(tmp_path / "dashboard.duckdb")
    before = path.read_bytes()
    real_connect = duckdb.connect
    tracked = TrackedConnection(real_connect(str(path), read_only=True), error)
    calls = []

    def connect(database: str, *, read_only: bool) -> TrackedConnection:
        calls.append((database, read_only))
        return tracked

    monkeypatch.setattr(dashboard_read.duckdb, "connect", connect)
    if error is None:
        load_dashboard_snapshot(
            database_path=path, cik="34", accession_number=ACCESSION_B
        )
        assert tracked.queries[-1][0] == "COMMIT"
    else:
        expected = (
            DashboardReadError if isinstance(error, duckdb.Error) else RuntimeError
        )
        with pytest.raises(expected, match=str(error)):
            load_dashboard_snapshot(
                database_path=path, cik="34", accession_number=ACCESSION_B
            )
        assert not any(sql == "COMMIT" for sql, _ in tracked.queries)
    assert calls == [(str(path.resolve()), True)] and tracked.closed
    assert tracked.queries[0][0] == "BEGIN TRANSACTION"
    filtered = [
        (sql, params) for sql, params in tracked.queries if "WHERE cik = ?" in sql
    ]
    assert all(
        params == [CIK_B, ACCESSION_B] and CIK_B not in sql and ACCESSION_B not in sql
        for sql, params in filtered
    )
    assert len(filtered) == 3
    with pytest.raises(duckdb.Error):
        tracked.connection.execute("SELECT 1")
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "error",
    [duckdb.IOException("Database is locked"), PermissionError("Access denied")],
)
def test_expected_connect_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")

    def connect(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(dashboard_read.duckdb, "connect", connect)
    with pytest.raises(DashboardReadError, match=str(error)):
        load_dashboard_snapshot(database_path=path)


def test_expected_filesystem_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")

    def resolve(*args: object, **kwargs: object) -> None:
        raise PermissionError("Access denied")

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(DashboardReadError, match="Access denied"):
        load_dashboard_snapshot(database_path=path)


def test_public_exports() -> None:
    import sec_edgar_lakehouse as package

    for name in (
        "DashboardReadError",
        "DashboardTable",
        "DashboardSnapshot",
        "load_dashboard_snapshot",
    ):
        assert name in package.__all__
    assert package.DashboardSnapshot is DashboardSnapshot
