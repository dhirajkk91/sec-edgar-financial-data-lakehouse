"""The Insights view, its computed headlines and its read-only loader.

The same three companies as the Companies tests: Alpha grew and converts well,
Beta Bank shrank and made a loss, and Gamma has a single year and no free cash flow.
"""

from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from sec_edgar_lakehouse import InsightsSnapshot, load_insights_snapshot
from sec_edgar_lakehouse.dashboard import insights_page as page
from sec_edgar_lakehouse.dashboard.dashboard_read import (
    _COMPANY_INSIGHTS_SCHEMA,
    DashboardReadError,
)
from tests.dashboard.test_company_page import ALPHA, BETA, GAMMA, add_company_tables
from tests.dashboard.test_dashboard_read import create_database

ROOT = Path(__file__).resolve().parents[2]
INSIGHTS = [
    # cik, years, revenues, cagr, net income, fcf, margins, conversion, coverage, issues
    (
        ALPHA,
        2024,
        2025,
        "100",
        "200",
        1.0,
        "30",
        "40",
        0.15,
        0.2,
        4 / 3,
        12,
        16,
        0.75,
        1,
        2,
    ),
    (
        BETA,
        2023,
        2025,
        "100",
        "81",
        -0.1,
        "-5",
        "3",
        -5 / 81,
        3 / 81,
        None,
        8,
        14,
        8 / 14,
        0,
        0,
    ),
    (GAMMA, 2025, 2025, "50", "50", None, "5", None, 0.1, None, None, 2, 4, 0.5, 0, 1),
]
# Two cloud companies over their first and latest fiscal years, in USD billions.
CAPEX = [
    {
        "company": "AMZN",
        "fiscal_year": 2021,
        "capex": 61.1,
        "operating_cash_flow": 46.3e9,
    },
    {
        "company": "AMZN",
        "fiscal_year": 2025,
        "capex": 131.8,
        "operating_cash_flow": 139.5e9,
    },
    {
        "company": "MSFT",
        "fiscal_year": 2022,
        "capex": 23.9,
        "operating_cash_flow": 89.0e9,
    },
    {
        "company": "MSFT",
        "fiscal_year": 2026,
        "capex": 115.9,
        "operating_cash_flow": 182.9e9,
    },
]


def add_insight_table(path: Path) -> Path:
    """The company_insights mart with its exact column types and the rows above."""
    with duckdb.connect(str(path)) as connection:
        fields = ", ".join(
            f"{column} {kind}" for column, kind in _COMPANY_INSIGHTS_SCHEMA
        )
        connection.execute(f"CREATE TABLE gold.company_insights ({fields})")
        marks = ", ".join("?" for _ in _COMPANY_INSIGHTS_SCHEMA)
        connection.executemany(
            f"INSERT INTO gold.company_insights VALUES ({marks})", INSIGHTS
        )
    return path


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = create_database(tmp_path / "insights.duckdb")
    return add_insight_table(add_company_tables(path))


@pytest.fixture
def snapshot(database: Path) -> InsightsSnapshot:
    return load_insights_snapshot(database_path=database)


@pytest.fixture
def rows(snapshot: InsightsSnapshot) -> list[page.Record]:
    return page.insight_rows(snapshot)


def test_loader_reads_insights_and_financials_in_order(
    snapshot: InsightsSnapshot,
) -> None:
    assert [row[0] for row in snapshot.insights.rows] == [ALPHA, BETA, GAMMA]
    assert len(snapshot.financials.rows) == 8
    latest = page.records(snapshot.insights)[0]["latest_revenue"]
    assert latest == Decimal("200.000000000000000000")


def test_loader_names_the_missing_insight_table(tmp_path: Path) -> None:
    path = add_company_tables(create_database(tmp_path / "old.duckdb"))

    with pytest.raises(DashboardReadError, match="Rebuild Gold") as error:
        load_insights_snapshot(database_path=path)
    assert "missing gold.company_insights)" in str(error.value)


def test_rows_use_the_ticker_or_the_sec_name(rows: list[page.Record]) -> None:
    assert [(r["company"], r["years"]) for r in rows] == [
        ("ALP", "FY2024–FY2025"),
        ("Beta Bank", "FY2023–FY2025"),
        ("GAM", "FY2025–FY2025"),
    ]


def test_growth_story_names_the_leader_and_a_shrinking_company(
    rows: list[page.Record],
) -> None:
    assert page.growth_story(rows) == (
        "ALP grew revenue 2.0× in one year, the fastest of the 2",
        (
            "The median company grew 45.0% a year, and 1 of 2 grew faster than 20% a "
            "year. Beta Bank shrank 10.0% a year. "
            "No rate for GAM: fewer than two years of revenue."
        ),
    )


def test_profit_story_counts_companies_below_the_line(rows: list[page.Record]) -> None:
    assert page.profit_story(rows) == (
        "ALP kept 15 cents of every revenue dollar as profit",
        (
            "0 of 2 companies had a net margin above 30%. 0 sit below the dashed line: "
            "their free cash flow was smaller than their profit. "
            "Not shown, missing a margin: GAM."
        ),
    )


def test_cash_story_gives_each_missing_reason(rows: list[page.Record]) -> None:
    headline, text = page.cash_story(rows)

    assert headline == "1 of 1 companies turned all of their profit into free cash"
    assert text.endswith("Left out: Beta Bank (a loss) and GAM (no free cash flow).")


def test_capex_rows_keep_annual_values_for_the_chosen_tickers(
    snapshot: InsightsSnapshot,
) -> None:
    assert page.capex_rows(snapshot, tickers=("ALP",)) == [
        {
            "company": "ALP",
            "fiscal_year": 2025,
            "capex": pytest.approx(10 / 1e9),
            "operating_cash_flow": 50.0,
        }
    ]
    assert page.capex_rows(snapshot) == []


def test_capex_story_compares_first_and_latest_years() -> None:
    assert page.capex_story(CAPEX) == (
        "The two cloud companies' capex rose 2.9× to $248B a year",
        (
            "Combined, up from $85B in each company's first year here. AMZN spent 94% "
            "of its operating cash flow on capex in FY2025."
        ),
    )
    assert page.capex_story([]) is None


def test_headline_numbers_follow_the_data(rows: list[page.Record]) -> None:
    assert page.headline_numbers(rows, CAPEX) == [
        ("Fastest revenue growth", "100.0% a year", "ALP, FY2024–FY2025"),
        ("Highest net margin", "15.0%", "ALP, FY2025"),
        ("Turned all profit into cash", "1 of 1", "companies, latest fiscal year"),
        ("Cloud capex, latest year", "$248B", "AMZN, MSFT · 2.9× their first year"),
    ]
    assert len(page.headline_numbers(rows, [])) == 3


def test_stories_handle_no_data() -> None:
    assert page.growth_story([])[1] == "No company has two fiscal years of revenue yet."
    assert page.cash_story([])[1].startswith("No company has positive")
    assert page.headline_numbers([], []) == []
    assert page.count_word(4) == "four" and page.count_word(12) == "12"


def test_charts_leave_out_companies_without_a_value(rows: list[page.Record]) -> None:
    def companies(chart: object) -> set[str]:
        spec = chart.to_dict()  # type: ignore[attr-defined]
        data = spec.get("data") or spec["layer"][0]["data"]
        return {value["company"] for value in data["values"]}

    assert companies(page.growth_chart(rows)) == {"ALP", "Beta Bank"}
    assert companies(page.conversion_chart(rows)) == {"ALP"}
    assert companies(page.capex_chart(CAPEX)) == {"AMZN", "MSFT"}


def test_bullets_are_computed_from_the_rows(rows: list[page.Record]) -> None:
    assert page.growth_leaders(rows) == [
        "**ALP** grew 100.0% a year, from $100 to $200 (FY2024–FY2025).",
        "**Beta Bank** shrank 10.0% a year, from $100 to $81 (FY2023–FY2025).",
    ]
    assert page.margin_leaders(rows) == [
        (
            "**ALP** kept 15.0% of revenue as profit in FY2025; "
            "its free cash flow margin was 20.0%."
        ),
        "**GAM** kept 10.0% of revenue as profit in FY2025.",
    ]
    # One company with a conversion rate has no "other end" to compare.
    assert page.conversion_extremes(rows) == []
    assert page.capex_shares(CAPEX) == [
        "**AMZN** spent $131.8B in FY2025, 94% of its operating cash flow.",
        "**MSFT** spent $115.9B in FY2026, 63% of its operating cash flow.",
    ]


def test_charts_show_the_ends_of_a_long_ranking() -> None:
    many: list[page.Record] = [
        {"company": f"C{index:03}", "cagr": index / 100, "conversion": None}
        for index in range(100)
    ]
    shown = page.chart_rows(many, "cagr")

    assert len(shown) == page.TOP_SHOWN + page.BOTTOM_SHOWN
    assert shown[0]["company"] == "C099" and shown[-1]["company"] == "C000"
    assert page._list_names([f"N{i}" for i in range(9)]) == (
        "N0, N1, N2, N3, N4, N5 and 3 more"
    )


def test_insights_view_reads_as_a_report(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", str(database))
    result = AppTest.from_file(str(ROOT / "dashboard_app.py"), default_timeout=15).run()
    result.radio(key="view").set_value("Insights").run()

    assert not result.exception and not result.error
    assert result.title[0].value == "What the data shows"
    assert [metric.label for metric in result.metric] == [
        "Fastest revenue growth",
        "Highest net margin",
        "Turned all profit into cash",
    ]
    assert [header.value for header in result.subheader] == [
        "ALP grew revenue 2.0× in one year, the fastest of the 2",
        "ALP kept 15 cents of every revenue dollar as profit",
        "1 of 1 companies turned all of their profit into free cash",
    ]
    assert len(result.dataframe[0].value) == 3
    assert [expander.label for expander in result.expander] == [
        "Table view",
        "About this data",
    ]


def test_insights_view_explains_a_database_without_the_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = add_company_tables(create_database(tmp_path / "old.duckdb"))
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", str(path))
    result = AppTest.from_file(str(ROOT / "dashboard_app.py"), default_timeout=15).run()
    result.radio(key="view").set_value("Insights").run()

    assert not result.exception
    assert "Rebuild Gold" in result.error[0].value
