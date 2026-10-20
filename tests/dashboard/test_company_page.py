"""The Companies view, its pure helpers, and its read-only loader.

A tiny Gold database holds three companies: one complete, one without a ticker
and with a missing year, and one whose latest revenue Gold withheld.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from sec_edgar_lakehouse import CompanySnapshot, load_company_snapshot
from sec_edgar_lakehouse.dashboard import company_page as page
from sec_edgar_lakehouse.dashboard.dashboard_read import (
    _COMPANIES_SCHEMA,
    _COMPANY_FINANCIALS_SCHEMA,
    DashboardReadError,
    load_company_names,
)
from tests.dashboard.test_dashboard_read import create_database, insert_record

ROOT = Path(__file__).resolve().parents[2]
ALPHA, BETA, GAMMA = "0000000012", "0000000034", "0000000056"


def add_company_tables(path: Path) -> Path:
    """Create the two company marts with their exact column types and a few rows."""
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS gold")
        for name, schema in (
            ("dim_companies", _COMPANIES_SCHEMA),
            ("fct_company_financials", _COMPANY_FINANCIALS_SCHEMA),
        ):
            fields = ", ".join(f"{column} {kind}" for column, kind in schema)
            connection.execute(f"CREATE TABLE gold.{name} ({fields})")
        companies = [
            (
                ALPHA,
                "Alpha Corp",
                "ALP",
                "Technology",
                "Software",
                "7372",
                "1231",
                8,
                2024,
                2025,
            ),
            (
                BETA,
                "Beta Bank",
                None,
                "Finance",
                "Banks",
                "6021",
                "1231",
                8,
                2023,
                2025,
            ),
            (GAMMA, "Gamma Inc", "GAM", None, None, None, None, 4, 2025, 2025),
        ]
        connection.executemany(
            "INSERT INTO gold.dim_companies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            companies,
        )
        rows = [
            (ALPHA, 2024, "FY", "100", "10", None, None, None, None, []),
            (ALPHA, 2025, "FY", "200", "30", "50", "10", "40", "0.5", []),
            (ALPHA, 2025, "Q1", "40", None, None, None, None, None, []),
            (ALPHA, 2025, "Q4", "60", None, None, None, None, None, ["revenue"]),
            (BETA, 2023, "FY", "100", None, None, None, None, None, []),
            (BETA, 2024, "FY", None, None, None, None, None, None, []),
            (BETA, 2025, "FY", "150", None, None, None, None, None, []),
            (GAMMA, 2025, "FY", None, "5", None, None, None, None, []),
        ]
        for (
            cik,
            year,
            period,
            revenue,
            income,
            ocf,
            capex,
            fcf,
            growth,
            derived,
        ) in rows:
            connection.execute(
                "INSERT INTO gold.fct_company_financials VALUES "
                "(?, ?, ?, 'acc', '10-K', ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, "
                "?, NULL, ?, ?)",
                [
                    cik,
                    year,
                    period,
                    date(year, 12, 31),
                    date(year, 1, 1),
                    date(year, 12, 31),
                    revenue,
                    income,
                    ocf,
                    capex,
                    fcf,
                    None
                    if revenue is None or income is None
                    else float(income) / float(revenue),
                    None if growth is None else float(growth),
                    derived,
                ],
            )
    return path


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return add_company_tables(create_database(tmp_path / "companies.duckdb"))


def snapshot_records(path: Path) -> tuple[list[page.Record], list[page.Record]]:
    snapshot = load_company_snapshot(database_path=path)
    return page.records(snapshot.companies), page.records(snapshot.financials)


def test_colors_follow_the_company_not_its_position() -> None:
    first = page.assign_colors(["a", "b", "c"], {})
    assert list(first.values()) == list(page.SERIES_COLORS[:3])
    # Removing "a" must not repaint "b" or "c"; a new company takes the freed slot.
    second = page.assign_colors(["b", "c", "d"], first)
    assert second["b"] == first["b"] and second["c"] == first["c"]
    assert second["d"] == first["a"]


def test_values_are_formatted_by_kind_and_missing_stays_visible() -> None:
    assert page.format_value(Decimal(416161000000), "usd") == "$416.2B"
    assert page.format_value(Decimal(-2500000000), "usd") == "-$2.5B"
    assert page.format_value(Decimal("7.46"), "per_share") == "$7.46"
    assert page.format_value(0.269, "ratio") == "26.9%"
    assert page.format_value(None, "usd") == "Not available"


def test_loader_keeps_exact_decimals(database: Path) -> None:
    snapshot = load_company_snapshot(database_path=database)
    assert isinstance(snapshot, CompanySnapshot)
    financials = page.records(snapshot.financials)
    revenue = [row["revenue"] for row in financials if row["cik"] == ALPHA]
    assert Decimal("200.000000000000000000") in revenue


def test_loader_explains_a_database_without_the_company_layer(tmp_path: Path) -> None:
    path = create_database(tmp_path / "old.duckdb")
    with pytest.raises(DashboardReadError, match="company_profile_load"):
        load_company_snapshot(database_path=path)
    assert load_company_names(database_path=path) == {}


def test_companies_are_ordered_by_latest_revenue(database: Path) -> None:
    companies, financials = snapshot_records(database)
    assert page.companies_by_latest_revenue(companies, financials) == [
        ALPHA,
        BETA,
        GAMMA,
    ]


def test_series_breaks_lines_at_gaps_and_marks_derived_points(database: Path) -> None:
    companies, financials = snapshot_records(database)
    labels = {str(c["cik"]): page.company_label(c) for c in companies}
    assert labels[BETA] == "Beta Bank"  # No SEC ticker: the name is used, not a guess.
    revenue = page.METRIC_BY_CODE["revenue"]
    annual = page.series_rows(financials, labels, [BETA], revenue, "Annual")
    assert [row["period"] for row in annual] == ["FY2023", "FY2025"]
    assert annual[0]["line"] != annual[1]["line"]  # FY2024 is missing, so no bridge.
    assert [row["is_last"] for row in annual] == [False, True]
    quarterly = page.series_rows(financials, labels, [ALPHA], revenue, "Quarterly")
    assert [(row["period"], row["derived"]) for row in quarterly] == [
        ("FY2025 Q1", False),
        ("FY2025 Q4", True),
    ]


def test_latest_year_table_uses_each_company_newest_fiscal_year(database: Path) -> None:
    companies, financials = snapshot_records(database)
    table = page.latest_annual_rows(financials, companies, [ALPHA, BETA, GAMMA])
    assert [row["Fiscal year"] for row in table] == [2025, 2025, 2025]
    assert table[0]["Free cash flow"] == pytest.approx(40 / 1e9)
    assert table[1]["Ticker"] == "—"
    assert table[2]["Revenue"] is None and table[2]["Sector"] == page.UNCLASSIFIED


def test_companies_view_is_the_default_and_filters_reset(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    insert_record(database, "dim_filings")  # One filing for Alpha, for the explorer.
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", str(database))
    result = AppTest.from_file(str(ROOT / "dashboard_app.py"), default_timeout=15).run()
    assert not result.exception
    assert result.title[0].value == "Company financials from SEC filings"
    assert result.multiselect(key="company_filter").value == [ALPHA, BETA, GAMMA]
    assert len(result.dataframe[0].value) == 3
    assert any("No latest-year value for GAM" in item.value for item in result.caption)
    result.multiselect(key="sector_filter").select("Finance").run()
    assert result.multiselect(key="company_filter").value == [BETA]
    result.selectbox(key="metric_choice").select("net_margin").run()
    result.radio(key="period_choice").set_value("Quarterly").run()
    assert not result.exception
    result.radio(key="view").set_value("Trace a filing").run()
    assert not result.exception
    assert "Alpha Corp (0000000012)" in result.selectbox(key="company_cik").options


def test_companies_view_shows_a_clear_error_for_an_old_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = create_database(tmp_path / "old.duckdb")
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", str(path))
    result = AppTest.from_file(str(ROOT / "dashboard_app.py"), default_timeout=15).run()
    assert not result.exception
    assert "company_profile_load" in result.error[0].value
