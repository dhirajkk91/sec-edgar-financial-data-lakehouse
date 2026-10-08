"""Optional UI boundary tests; financial policy remains in the Gold models."""

import hashlib
import json
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest
from test_dashboard_read import (
    ACCESSION_A,
    ACCESSION_B,
    CIK_A,
    CIK_B,
    EXACT,
    create_database,
    insert_record,
)

from sec_edgar_lakehouse import dashboard_app
from sec_edgar_lakehouse.dashboard_read import load_dashboard_snapshot

ROOT = Path(__file__).resolve().parents[1]
PARTIAL = "0000000012-25-000003"
FAILED = "0000000012-25-000001"
TINY = Decimal("0.000000000000000001")
EXACT_DISPLAY = "12,345,678,901,234,567,890.123456789012345678"
USD = "{http://www.xbrl.org/2003/iso4217}USD"
USD_PER_SHARE = "({http://www.xbrl.org/2003/iso4217}USD) / ({http://www.xbrl.org/2003/instance}shares)"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = create_database(tmp_path / "dashboard.duckdb")
    for accession, status, missing in (
        (ACCESSION_A, "COMPLETE", []),
        (PARTIAL, "PARTIAL", ["net_income", "diluted_eps"]),
        (FAILED, "FAILED", None),
    ):
        insert_record(
            path,
            "dim_filings",
            accession_number=accession,
            form="10-Q",
            gold_quality_status=status,
            gold_quality_reason=f"Stored reason for {status}",
            missing_critical_metric_codes=missing,
            warning_issue_count=2 if status == "COMPLETE" else 0,
            error_issue_count=0 if status == "COMPLETE" else 1,
        )
    insert_record(
        path,
        "dim_filings",
        cik=CIK_B,
        accession_number=ACCESSION_B,
        form="10-K",
        gold_quality_status="COMPLETE",
    )
    for label, start, end, instant, value in (
        ("annual", date(2024, 7, 1), date(2025, 6, 30), None, EXACT),
        ("quarter", date(2025, 4, 1), date(2025, 6, 30), None, TINY),
        ("year_to_date", date(2025, 1, 1), date(2025, 6, 30), None, EXACT),
        ("instant", None, None, date(2025, 6, 30), None),
        ("derived_quarter", date(2025, 4, 1), date(2025, 6, 30), None, TINY),
    ):
        insert_record(
            path,
            "fct_financial_metrics",
            metric_code="revenue",
            period_label=label,
            period_start=start,
            period_end=end,
            period_instant=instant,
            value_origin="derived" if label == "derived_quarter" else "reported",
            value_decimal=value,
            unit_expression="USD",
            current_source_document_name="current.htm",
            current_source_sha256="current-checksum",
            current_source_occurrence_id="current-id",
            current_supporting_occurrence_ids=["second", "first", "second"],
            previous_accession_number=FAILED,
            previous_source_document_name="previous.htm",
            previous_source_sha256="previous-checksum",
            previous_source_occurrence_id="previous-id",
            previous_value_decimal=TINY,
            previous_supporting_occurrence_ids=[],
            derivation_method="stored_method",
        )
    # Equal records still need distinct selectable occurrence identities.
    insert_record(
        path,
        "fct_financial_metrics",
        metric_code="revenue",
        period_label="quarter",
        period_start=date(2025, 4, 1),
        period_end=date(2025, 6, 30),
        period_instant=None,
        value_origin="reported",
        value_decimal=TINY,
        unit_expression="USD",
    )
    insert_record(
        path,
        "fct_financial_metrics",
        cik=CIK_B,
        accession_number=ACCESSION_B,
        metric_code="diluted_eps",
        period_label="annual",
        value_origin="reported",
        unit_expression=USD_PER_SHARE,
        value_decimal=Decimal("3.720000000000000000"),
    )
    for _ in range(2):
        insert_record(
            path,
            "metric_quality_issues",
            severity="WARNING",
            issue_code="stored_warning",
            source_occurrence_ids=["second", "first", "second"],
            previous_source_occurrence_ids=None,
        )
    insert_record(
        path,
        "metric_quality_issues",
        accession_number=PARTIAL,
        severity="ERROR",
        source_occurrence_ids=[],
        previous_source_occurrence_ids=None,
    )
    return path


def app(monkeypatch: pytest.MonkeyPatch, path: Path) -> AppTest:
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", str(path))
    result = AppTest.from_file(str(ROOT / "dashboard_app.py"), default_timeout=15).run()
    assert not result.exception
    return result


def select_filing(result: AppTest, cik: str, accession: str) -> AppTest:
    result.selectbox(key="company_cik").select(cik).run()
    selector = result.selectbox(key="filing_record")
    index = next(i for i, label in enumerate(selector.options) if accession in label)
    selector.select(index).run()
    assert not result.exception
    return result


def json_records(result: AppTest) -> list[dict[str, object]]:
    values = [json.loads(element.value) for element in result.json]
    return [value for value in values if isinstance(value, dict)]


def metric_detail(result: AppTest) -> dict[str, object]:
    return next(row for row in json_records(result) if "value_decimal" in row)


def test_explicit_selection_and_company_filing_isolation(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    result = app(monkeypatch, database)
    assert result.selectbox(key="company_cik").value is None
    assert result.selectbox(key="company_cik").options == [CIK_A, CIK_B]
    assert not result.dataframe
    result.selectbox(key="company_cik").select(CIK_A).run()
    assert result.selectbox(key="filing_record").value is None
    filings = result.dataframe[0].value
    assert set(filings.accession_number) == {ACCESSION_A, PARTIAL, FAILED}
    assert set(filings.gold_quality_status) == {"COMPLETE", "PARTIAL", "FAILED"}
    assert not result.metric
    select_filing(result, CIK_A, ACCESSION_A)
    assert result.selectbox(key="metric_record").value is None
    assert len(result.metric) == 3  # Filing counts only; no financial value chosen.
    assert len(result.dataframe[1].value) == 6
    select_filing(result, CIK_B, ACCESSION_B)
    assert result.dataframe[0].value.accession_number.tolist() == [ACCESSION_B]
    assert result.dataframe[1].value.metric_code.tolist() == ["Diluted EPS"]
    assert result.dataframe[1].value.unit_expression.tolist() == ["USD/share"]
    assert result.dataframe[1].value.value_decimal.tolist() == ["3.72"]
    result.selectbox(key="metric_record").select(0).run()
    assert result.metric[3].label == "Diluted EPS"
    assert result.metric[3].value == "3.72"
    assert metric_detail(result)["value_decimal"] == "3.720000000000000000"
    assert metric_detail(result)["unit_expression"] == USD_PER_SHARE
    assert json_records(result)[0]["cik"] == CIK_B
    assert json_records(result)[0]["accession_number"] == ACCESSION_B
    assert not any("issue_code" in row for row in json_records(result))


def test_exact_periods_decimals_and_full_lineage(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    result = select_filing(app(monkeypatch, database), CIK_A, ACCESSION_A)
    overview = result.dataframe[1].value
    assert set(overview.period_label) == {
        "annual",
        "quarter",
        "year_to_date",
        "instant",
        "derived_quarter",
    }
    assert EXACT_DISPLAY in overview.value_decimal.tolist()
    assert format(TINY, "f") in overview.value_decimal.tolist()
    assert (
        overview.loc[overview.period_label == "instant", "value_decimal"].isna().all()
    )
    labels = result.selectbox(key="metric_record").options
    assert len(labels) == len(set(labels)) == 6
    assert any("2025-04-01 → 2025-06-30" in label for label in labels)
    assert any("instant 2025-06-30" in label for label in labels)
    snapshot = load_dashboard_snapshot(
        database_path=database, cik=CIK_A, accession_number=ACCESSION_A
    )
    for index, row in enumerate(snapshot.financial_metrics.rows):
        result.selectbox(key="metric_record").select(index).run()
        assert not result.exception
        expected = dict(zip(snapshot.financial_metrics.columns, row, strict=True))
        detail = metric_detail(result)
        assert detail == {
            column: (
                format(value, "f")
                if isinstance(value, Decimal)
                else value.isoformat()
                if isinstance(value, date)
                else list(value)
                if isinstance(value, tuple)
                else value
            )
            for column, value in expected.items()
        }
        displayed = result.metric[3].value
        value = expected["value_decimal"]
        assert value is None or isinstance(value, Decimal)
        assert (
            displayed
            == {
                None: "Unavailable",
                EXACT: EXACT_DISPLAY,
                TINY: "0.000000000000000001",
            }[value]
        )
        assert result.metric[3].label == "Revenue"
        if expected["value_origin"] == "derived":
            assert result.metric[4].value == EXACT_DISPLAY
            assert result.metric[5].value == format(TINY, "f")
            assert detail["current_supporting_occurrence_ids"] == [
                "second",
                "first",
                "second",
            ]
            assert detail["previous_supporting_occurrence_ids"] == []
            assert detail["previous_source_document_name"] == "previous.htm"
            assert detail["previous_source_sha256"] == "previous-checksum"


def test_parent_and_display_filter_changes_reset_details(
    monkeypatch: pytest.MonkeyPatch, database: Path, tmp_path: Path
) -> None:
    result = select_filing(app(monkeypatch, database), CIK_A, ACCESSION_A)
    result.selectbox(key="metric_record").select(0).run()
    assert metric_detail(result)
    result.selectbox(key="period_label").select("quarter").run()
    assert result.selectbox(key="metric_record").value is None
    assert len(result.dataframe[1].value) == 2
    assert set(result.dataframe[1].value.period_label) == {"quarter"}
    assert not any("value_decimal" in row for row in json_records(result))
    result.selectbox(key="metric_record").select(1).run()
    assert metric_detail(result)["period_label"] == "quarter"
    selector = result.selectbox(key="filing_record")
    selector.select(
        next(i for i, label in enumerate(selector.options) if PARTIAL in label)
    ).run()
    assert not any("value_decimal" in row for row in json_records(result))
    select_filing(result, CIK_A, ACCESSION_A)
    assert result.selectbox(key="metric_record").value is None
    assert result.selectbox(key="period_label").value is None
    result.selectbox(key="metric_record").select(0).run()
    result.selectbox(key="company_cik").select(CIK_B).run()
    assert result.selectbox(key="filing_record").value is None
    assert not result.json
    select_filing(result, CIK_B, ACCESSION_B)
    result.selectbox(key="metric_record").select(0).run()
    other = create_database(tmp_path / "other.duckdb")
    insert_record(other, "dim_filings", cik=CIK_A)
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", str(other))
    result.run()
    assert not result.exception
    assert result.selectbox(key="company_cik").value is None
    assert result.selectbox(key="company_cik").options == [CIK_A]
    assert not result.json


def test_quality_states_occurrences_and_array_states(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    result = select_filing(app(monkeypatch, database), CIK_A, ACCESSION_A)
    records = json_records(result)
    assert records[0]["gold_quality_status"] == "COMPLETE"
    assert records[0]["missing_critical_metric_codes"] == []
    assert records[0]["warning_issue_count"] == 2
    assert result.success[0].value == "COMPLETE: Stored reason for COMPLETE"
    assert any(
        "Missing critical metrics: None" in item.value for item in result.markdown
    )
    issues = [row for row in records if "issue_code" in row]
    assert len(issues) == 2 and issues[0] == issues[1]
    assert issues[0]["source_occurrence_ids"] == ["second", "first", "second"]
    assert issues[0]["previous_source_occurrence_ids"] is None
    assert len(result.warning) == 2
    select_filing(result, CIK_A, PARTIAL)
    records = json_records(result)
    assert records[0]["gold_quality_status"] == "PARTIAL"
    assert records[0]["missing_critical_metric_codes"] == ["net_income", "diluted_eps"]
    assert len(result.warning) == 1
    assert result.warning[0].value == "PARTIAL: Stored reason for PARTIAL"
    assert any("Net income, Diluted EPS" in item.value for item in result.markdown)
    assert len(result.error) == 1
    assert records[1]["source_occurrence_ids"] == []
    assert all(selector.key != "metric_record" for selector in result.selectbox)
    assert not any("value_decimal" in row for row in records)
    select_filing(result, CIK_A, FAILED)
    assert json_records(result)[0]["missing_critical_metric_codes"] is None
    assert len(result.error) == 1
    assert result.error[0].value == "FAILED: Stored reason for FAILED"
    assert any(
        "Missing critical metrics: Unavailable" in item.value
        for item in result.markdown
    )
    assert len(result.dataframe) == 1


def test_one_unfiltered_read_per_rerun_and_database_unchanged(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    calls: list[Path] = []

    def load(*, database_path: Path):
        calls.append(database_path)
        return load_dashboard_snapshot(database_path=database_path)

    monkeypatch.setattr(dashboard_app, "load_dashboard_snapshot", load)
    result = app(monkeypatch, database)
    assert len(calls) == 1
    select_filing(result, CIK_A, ACCESSION_A)
    assert len(calls) == 3
    result.selectbox(key="metric_record").select(0).run()
    assert len(calls) == 4
    result.selectbox(key="period_label").select("year_to_date").run()
    assert len(calls) == 5
    assert all(path == database for path in calls)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_empty_and_expected_read_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = create_database(tmp_path / "empty.duckdb")
    result = app(monkeypatch, empty)
    assert not result.selectbox and not result.dataframe
    assert result.info
    missing = tmp_path / "missing.duckdb"
    result = app(monkeypatch, missing)
    assert result.error and not missing.exists()
    assert str(missing) in result.error[0].value


def test_programming_exceptions_are_visible(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    def broken(*, database_path: Path):
        raise RuntimeError("Unexpected programming failure")

    monkeypatch.setattr(dashboard_app, "load_dashboard_snapshot", broken)
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", str(database))
    result = AppTest.from_file(str(ROOT / "dashboard_app.py")).run()
    assert len(result.exception) == 1
    assert result.exception[0].message == "Unexpected programming failure"


@pytest.mark.parametrize("configured", ["", "   ", "data/relative.duckdb"])
def test_entrypoint_resolves_paths_against_repository(
    monkeypatch: pytest.MonkeyPatch, database: Path, configured: str
) -> None:
    calls: list[Path] = []

    def load(*, database_path: Path):
        calls.append(database_path)
        return load_dashboard_snapshot(database_path=database)

    monkeypatch.setattr(dashboard_app, "load_dashboard_snapshot", load)
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", configured)
    result = AppTest.from_file(str(ROOT / "dashboard_app.py")).run()
    assert not result.exception
    expected = ROOT / (configured.strip() or "data/query/sec_edgar.duckdb")
    assert calls == [expected.resolve()]


def test_core_imports_and_inspector_without_streamlit() -> None:
    code = """
import importlib.abc
import sys
class NoStreamlit(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'streamlit' or fullname.startswith('streamlit.'):
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, NoStreamlit())
import sec_edgar_lakehouse
import sec_edgar_lakehouse.dashboard_inspect
assert 'streamlit' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "value,expected",
    [
        ("12715000000.000000000000000000", "12,715,000,000"),
        ("1200.000000000000000000", "1,200"),
        ("-1200.010000000000000000", "-1,200.01"),
        (str(EXACT), EXACT_DISPLAY),
        ("3.720000000000000000", "3.72"),
        ("0.000000000000000001", "0.000000000000000001"),
        ("-0.000000000000000001", "-0.000000000000000001"),
        ("1000.000000010000000000", "1,000.00000001"),
        ("0.000000000000000000", "0"),
        ("-0.000000000000000000", "-0"),
        (None, "Unavailable"),
    ],
)
def test_financial_format_preserves_digits_and_fixed_scale_lineage(
    value: str | None, expected: str
) -> None:
    decimal = None if value is None else Decimal(value)
    assert dashboard_app._financial_text(decimal) == expected
    assert dashboard_app._overview_value("value_decimal", decimal) == (
        None if value is None else expected
    )
    assert dashboard_app._present_record({"value_decimal": decimal}) == {
        "value_decimal": value
    }


@pytest.mark.parametrize(
    "code,label",
    [
        ("revenue", "Revenue"),
        ("net_income", "Net income"),
        ("diluted_eps", "Diluted EPS"),
        ("total_assets", "Total assets"),
        ("total_liabilities", "Total liabilities"),
        ("cash_and_equivalents", "Cash and equivalents"),
        ("operating_cash_flow", "Operating cash flow"),
        ("capital_expenditures", "Capital expenditures"),
        ("unrecognized_metric", "unrecognized_metric"),
    ],
)
def test_metric_labels_preserve_raw_codes(code: str, label: str) -> None:
    assert dashboard_app._metric_name(code) == label
    assert dashboard_app._overview_value("metric_code", code) == label
    assert dashboard_app._missing_metrics_text((code, code)) == f"{label}, {label}"
    assert dashboard_app._present_record({"metric_code": code}) == {"metric_code": code}


@pytest.mark.parametrize(
    "expression,label",
    [
        (USD, "USD"),
        (USD_PER_SHARE, "USD/share"),
        ("USD", "USD"),
        ("USD/share", "USD/share"),
        (USD_PER_SHARE.replace(" / ", "/"), USD_PER_SHARE.replace(" / ", "/")),
        (
            USD_PER_SHARE.replace("shares", "share"),
            USD_PER_SHARE.replace("shares", "share"),
        ),
        (USD.replace("USD", "EUR"), USD.replace("USD", "EUR")),
        (f" {USD}", f" {USD}"),
        (None, None),
    ],
)
def test_only_exact_known_unit_expressions_are_mapped(
    expression: str | None, label: str | None
) -> None:
    assert dashboard_app._unit_label(expression) == label
    assert dashboard_app._overview_value("unit_expression", expression) == label
    assert dashboard_app._present_record({"unit_expression": expression}) == {
        "unit_expression": expression
    }


@pytest.mark.parametrize(
    "status,reason,description,presentation",
    [
        (
            "COMPLETE",
            "CRITICAL_METRICS_PRESENT",
            "All required financial metrics are present.",
            "success",
        ),
        (
            "PARTIAL",
            "MISSING_CRITICAL_METRICS",
            "Some required financial metrics are missing.",
            "warning",
        ),
        (
            "PARTIAL",
            "METRIC_ERRORS",
            "Financial metric errors are recorded for this filing.",
            "warning",
        ),
        (
            "FAILED",
            "MISSING_FILING_METADATA",
            "Filing metadata is unavailable.",
            "error",
        ),
        (
            "FAILED",
            "MISSING_REPORT_DATE",
            "The filing report date is unavailable.",
            "error",
        ),
        ("FAILED", "UNSUPPORTED_FORM", "The filing form is not supported.", "error"),
        (
            "FAILED",
            "NO_SELECTED_METRICS",
            "No financial metrics were selected for this filing.",
            "error",
        ),
        (
            "PARTIAL",
            "CRITICAL_METRICS_PRESENT",
            "All required financial metrics are present.",
            "warning",
        ),
        ("COMPLETE", "unrecognized_reason", "unrecognized_reason", "success"),
    ],
)
def test_quality_descriptions_use_stored_status_and_keep_raw_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    reason: str,
    description: str,
    presentation: str,
) -> None:
    path = create_database(tmp_path / "quality.duckdb")
    insert_record(
        path, "dim_filings", gold_quality_status=status, gold_quality_reason=reason
    )
    result = select_filing(app(monkeypatch, path), CIK_A, ACCESSION_A)
    assert getattr(result, presentation)[0].value == f"{status}: {description}"
    raw = json_records(result)[0]
    assert raw["gold_quality_status"] == status
    assert raw["gold_quality_reason"] == reason


def test_tables_have_native_readable_labels_without_changing_columns(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    result = select_filing(app(monkeypatch, database), CIK_A, ACCESSION_A)
    for table in result.dataframe:
        configuration = json.loads(table.proto.columns)
        assert set(configuration) - {"_index"} == set(table.value.columns)
        assert all(configuration[column]["label"] for column in table.value.columns)
    filing_labels = json.loads(result.dataframe[0].proto.columns)
    assert filing_labels["fiscal_year_focus"]["label"] == "Fiscal year"
    assert filing_labels["financial_metric_row_count"]["label"] == "Financial rows"
    assert filing_labels["error_issue_count"]["label"] == "Errors"
    assert filing_labels["warning_issue_count"]["label"] == "Warnings"
    metric_labels = json.loads(result.dataframe[1].proto.columns)
    assert {
        column: metric_labels[column]["label"]
        for column in (
            "period_start",
            "period_end",
            "period_instant",
            "unit_expression",
            "value_origin",
        )
    } == {
        "period_start": "Period start",
        "period_end": "Period end",
        "period_instant": "Instant date",
        "unit_expression": "Units",
        "value_origin": "Origin",
    }
