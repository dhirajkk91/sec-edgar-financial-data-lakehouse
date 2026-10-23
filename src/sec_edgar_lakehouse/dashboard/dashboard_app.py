"""Two views of the stored Gold results.

Companies compares companies' financials across fiscal years. Trace a filing
lets the user follow one filing's values back to their sources.

Each rerun uses one read-only snapshot, then filters it in memory.
Table labels and shortened number text are only for display. Full detail
records keep the original fields, complete decimal text and evidence order.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import streamlit as st

from sec_edgar_lakehouse.dashboard import company_page
from sec_edgar_lakehouse.dashboard.dashboard_read import (
    DashboardReadError,
    DashboardSnapshot,
    DashboardTable,
    load_company_names,
    load_company_snapshot,
    load_dashboard_snapshot,
)

Record = dict[str, object]

METRIC_LABELS = {
    "revenue": "Revenue",
    "net_income": "Net income",
    "diluted_eps": "Diluted EPS",
    "total_assets": "Total assets",
    "total_liabilities": "Total liabilities",
    "cash_and_equivalents": "Cash and equivalents",
    "operating_cash_flow": "Operating cash flow",
    "capital_expenditures": "Capital expenditures",
}
UNIT_LABELS = {
    "{http://www.xbrl.org/2003/iso4217}USD": "USD",
    "({http://www.xbrl.org/2003/iso4217}USD) / ({http://www.xbrl.org/2003/instance}shares)": "USD/share",
}
QUALITY_REASONS = {
    "MISSING_FILING_METADATA": "Filing metadata is unavailable.",
    "MISSING_REPORT_DATE": "The filing report date is unavailable.",
    "UNSUPPORTED_FORM": "The filing form is not supported.",
    "NO_SELECTED_METRICS": "No financial metrics were selected for this filing.",
    "MISSING_CRITICAL_METRICS": "Some required financial metrics are missing.",
    "METRIC_ERRORS": "Financial metric errors are recorded for this filing.",
    "CRITICAL_METRICS_PRESENT": "All required financial metrics are present.",
}
COLUMN_LABELS = {
    "accession_number": "Accession",
    "form": "Form",
    "filing_date": "Filing date",
    "report_date": "Report date",
    "fiscal_year_focus": "Fiscal year",
    "fiscal_period_focus": "Fiscal focus",
    "gold_quality_status": "Gold quality",
    "financial_metric_row_count": "Financial rows",
    "error_issue_count": "Errors",
    "warning_issue_count": "Warnings",
    "metric_code": "Metric",
    "period_label": "Period label",
    "period_start": "Period start",
    "period_end": "Period end",
    "period_instant": "Instant date",
    "value_decimal": "Value",
    "unit_expression": "Units",
    "value_origin": "Origin",
    "severity": "Severity",
    "issue_stage": "Stage",
    "issue_code": "Issue code",
    "details": "Details",
}

FILING_COLUMNS = (
    "accession_number",
    "form",
    "filing_date",
    "report_date",
    "fiscal_year_focus",
    "fiscal_period_focus",
    "gold_quality_status",
    "financial_metric_row_count",
    "error_issue_count",
    "warning_issue_count",
)
METRIC_COLUMNS = (
    "metric_code",
    "period_label",
    "period_start",
    "period_end",
    "period_instant",
    "value_decimal",
    "unit_expression",
    "value_origin",
)
ISSUE_COLUMNS = (
    "severity",
    "issue_stage",
    "metric_code",
    "issue_code",
    "details",
    "report_date",
    "period_start",
    "period_end",
    "period_instant",
    "fiscal_year_focus",
    "fiscal_period_focus",
)


def _present(value: object) -> object:
    """Prepare full detail records, keeping complete decimal text and evidence order."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_present(item) for item in value]
    return value


def _present_record(record: Record) -> Record:
    """Build a separate display record so formatting never changes the snapshot."""
    return {column: _present(value) for column, value in record.items()}


def _records(
    table: DashboardTable, *, cik: str, accession: object = None
) -> list[Record]:
    """Filter the loaded rows by company and filing without changing their contents."""
    records = [dict(zip(table.columns, row, strict=True)) for row in table.rows]
    return [
        record
        for record in records
        if record["cik"] == cik
        and (accession is None or record["accession_number"] == accession)
    ]


def _reset_dependents(scope: str, value: object, *keys: str) -> None:
    """Reset widgets before rendering them when their parent scope changes."""
    # An old child selection may point at another filing after its parent changes.
    if st.session_state.get(scope) != value:
        for key in keys:
            st.session_state.pop(key, None)
    st.session_state[scope] = value


def _overview(records: list[Record], columns: tuple[str, ...]) -> None:
    """Show readable table columns while keeping the full records available in details."""
    st.dataframe(
        [
            {column: _overview_value(column, record[column]) for column in columns}
            for record in records
        ],
        column_config={
            column: st.column_config.Column(COLUMN_LABELS[column]) for column in columns
        },
        hide_index=True,
        width="stretch",
    )


def _text(value: object) -> str:
    """Use Unavailable for null fields; keep complete decimal text in general details."""
    return "Unavailable" if value is None else str(_present(value))


def _financial_text(value: object) -> str:
    """Add commas and trim fractional zeros as text, without rounding or using floats."""
    if not isinstance(value, Decimal):
        return _text(value)
    # Trim fractional zeros only; zeros in the integer and tiny values must stay.
    integer, separator, fraction = format(value, ",f").partition(".")
    fraction = fraction.rstrip("0")
    return integer + (separator + fraction if fraction else "")


def _metric_name(value: object) -> str:
    """Known metrics get readable names; an unfamiliar code stays visible as written."""
    return METRIC_LABELS.get(value, value) if isinstance(value, str) else _text(value)


def _unit_label(value: object) -> object:
    """Only known exact unit expressions get a label; do not guess what another unit means."""
    return UNIT_LABELS.get(value, value) if isinstance(value, str) else _present(value)


def _quality_reason(value: object) -> str:
    """Explain known reasons but keep an unknown reason visible instead of hiding it."""
    return QUALITY_REASONS.get(value, value) if isinstance(value, str) else _text(value)


def _overview_value(column: str, value: object) -> object:
    """Apply friendly labels and number text only to the overview table."""
    if column == "metric_code":
        return _metric_name(value) if value is not None else None
    if column == "unit_expression":
        return _unit_label(value)
    if isinstance(value, Decimal):
        return _financial_text(value)
    return _present(value)


def _missing_metrics_text(value: object) -> str:
    """Show None for an empty list and Unavailable when the list is missing."""
    if isinstance(value, (tuple, list)):
        return ", ".join(_metric_name(code) for code in value) if value else "None"
    return _text(value)


def _filing_label(record: Record) -> str:
    """Keep the accession visible so two filings with the same report date remain distinguishable."""
    return (
        f"{record['accession_number']} · {_text(record['form'])}"
        f" · report {_text(record['report_date'])}"
    )


def _metric_label(index: int, record: Record) -> str:
    """Include period boundaries and origin so reported and derived rows are easy to tell apart."""
    period = (
        f"instant {_text(record['period_instant'])}"
        if record["period_instant"] is not None
        else f"{_text(record['period_start'])} → {_text(record['period_end'])}"
    )
    return (
        f"{index + 1}. {_metric_name(record['metric_code'])} · {record['period_label']} · {period}"
        f" · {record['value_origin']}"
    )


def _filing_details(record: Record) -> None:
    """Show the stored quality result; readable labels do not change its status or reason."""
    st.subheader("Selected filing")
    st.caption(_filing_label(record))
    status = record["gold_quality_status"]
    message = f"{_text(status)}: {_quality_reason(record['gold_quality_reason'])}"
    if status == "FAILED":
        st.error(message)
    elif status == "PARTIAL":
        st.warning(message)
    elif status == "COMPLETE":
        st.success(message)
    else:
        st.info(message)
    st.write(
        "Missing critical metrics:",
        _missing_metrics_text(record["missing_critical_metric_codes"]),
    )
    counts = st.columns(3)
    counts[0].metric("Financial rows", _text(record["financial_metric_row_count"]))
    counts[1].metric("Errors", _text(record["error_issue_count"]))
    counts[2].metric("Warnings", _text(record["warning_issue_count"]))
    with st.expander("Filing metadata and source evidence"):
        st.json(_present_record(record))


def _financial_metrics(records: list[Record]) -> None:
    """Let users select exact periods and see the inputs behind a derived value."""
    st.subheader("Financial metrics")
    if not records:
        st.info("No financial metric records are available for this filing.")
        return
    period_labels = sorted(
        {
            str(record["period_label"])
            for record in records
            if record["period_label"] is not None
        }
    )
    period = st.selectbox(
        "Period label",
        period_labels,
        index=None,
        placeholder="All stored periods",
        key="period_label",
    )
    _reset_dependents("_period", period, "metric_record")
    displayed = (
        records
        if period is None
        else [r for r in records if r["period_label"] == period]
    )
    _overview(displayed, METRIC_COLUMNS)
    selection = st.selectbox(
        "Metric record — exact period and origin",
        range(len(displayed)),
        format_func=lambda index: _metric_label(index, displayed[index]),
        index=None,
        placeholder="Choose a metric record",
        key="metric_record",
    )
    if selection is None:
        st.info("Choose a metric record to see its exact value and source lineage.")
        return
    record = displayed[selection]
    st.metric(
        _metric_name(record["metric_code"]), _financial_text(record["value_decimal"])
    )
    st.caption(
        f"{_text(_unit_label(record['unit_expression']))} · {record['period_label']}"
        f" · {record['value_origin']}"
    )
    if record["value_origin"] == "derived":
        inputs = st.columns(2)
        inputs[0].metric(
            "Current input", _financial_text(record["current_value_decimal"])
        )
        inputs[1].metric(
            "Predecessor input", _financial_text(record["previous_value_decimal"])
        )
        st.write("Stored derivation method:", _text(record["derivation_method"]))
    with st.expander("Metric record and lineage", expanded=True):
        st.json(_present_record(record))


def _quality_issues(records: list[Record]) -> None:
    """Show the saved issues and evidence without applying new financial rules."""
    st.subheader("Quality issues")
    if not records:
        st.info("No quality issues are recorded for this filing.")
        return
    _overview(records, ISSUE_COLUMNS)
    for index, record in enumerate(records):
        message = (
            f"{index + 1}. {_text(record['severity'])} · {_text(record['issue_code'])}"
            f": {_text(record['details'])}"
        )
        if record["severity"] == "ERROR":
            st.error(message)
        elif record["severity"] == "WARNING":
            st.warning(message)
        with st.expander(
            f"Issue {index + 1} · {_text(record['severity'])} · {_text(record['issue_code'])}"
        ):
            st.json(_present_record(record))


VIEWS = ("Companies", "Trace a filing")
# The README traces this value. The explorer opens on it when the dataset has it,
# so a first visit shows a derived number with both inputs instead of an empty page.
EXAMPLE_CIK = "0000320193"
EXAMPLE_ACCESSION = "0000320193-25-000079"
EXAMPLE_METRIC = ("operating_cash_flow", "derived_quarter")


def _offer_example(snapshot: DashboardSnapshot) -> None:
    """Preselect the example record when the explorer opens, if the dataset has it.

    Clearing or changing the company afterwards does not bring the example back
    until the visitor leaves the explorer and opens it again.
    The widgets still hold row positions, so this uses the same filtered lists
    the page builds below.
    """
    if st.session_state.get("_example_offered"):
        return
    st.session_state["_example_offered"] = True
    filings = _records(snapshot.filings, cik=EXAMPLE_CIK)
    filing = next(
        (
            index
            for index, record in enumerate(filings)
            if record["accession_number"] == EXAMPLE_ACCESSION
        ),
        None,
    )
    metrics = _records(
        snapshot.financial_metrics, cik=EXAMPLE_CIK, accession=EXAMPLE_ACCESSION
    )
    metric = next(
        (
            index
            for index, record in enumerate(metrics)
            if (record["metric_code"], record["period_label"]) == EXAMPLE_METRIC
        ),
        None,
    )
    if filing is None or metric is None:
        return
    st.session_state.update(
        company_cik=EXAMPLE_CIK,
        _company=EXAMPLE_CIK,
        filing_record=filing,
        _filing=EXAMPLE_ACCESSION,
        metric_record=metric,
    )


def _companies(database_path: Path) -> None:
    """Render the company comparison view, or explain why it cannot be shown."""
    try:
        snapshot = load_company_snapshot(database_path=database_path)
    except DashboardReadError as exc:
        st.title("Company financials from SEC filings")
        st.error(f"Unable to read company financials: {exc}")
        st.info(
            "Check the configured database path and finish any pipeline writes first."
        )
        return
    company_page.render(snapshot)


def main(*, database_path: Path) -> None:
    """Show the chosen view: company comparison, or the filing explorer.

    In the explorer, filters use the loaded rows so the page stays consistent
    within a rerun. Changing a parent selection clears its child widgets.
    Expected read errors are shown on the page; unexpected errors still raise.
    """
    st.set_page_config(page_title="SEC EDGAR financials", layout="wide")
    view = st.sidebar.radio("View", VIEWS, key="view")
    st.sidebar.caption(f"Database: {database_path.name}")
    if view == "Companies":
        # Streamlit drops the explorer's widget values while it is hidden, so
        # offer the example again the next time the explorer opens.
        st.session_state.pop("_example_offered", None)
        _companies(database_path)
        return
    st.title("SEC filing explorer")
    _reset_dependents(
        "_database",
        str(database_path.resolve()),
        "company_cik",
        "filing_record",
        "period_label",
        "metric_record",
        "_company",
        "_filing",
        "_period",
    )
    try:
        snapshot = load_dashboard_snapshot(database_path=database_path)
    except DashboardReadError as exc:
        st.error(f"Unable to read the filing dataset: {exc}")
        st.info(
            "Check the configured database path and finish any pipeline writes first."
        )
        return
    st.caption(
        f"Available coverage: {len(snapshot.available_ciks)} CIKs · "
        f"{len(snapshot.filings.rows)} filings · "
        f"{len(snapshot.financial_metrics.rows)} financial rows · "
        f"{len(snapshot.quality_issues.rows)} issues"
    )
    st.write(
        "Explore the stored filing selection. Coverage may represent only part of a company's history."
    )
    if not snapshot.available_ciks:
        st.info("No filings are available in this dataset.")
        return
    _offer_example(snapshot)
    try:
        names = load_company_names(database_path=database_path)
    except DashboardReadError:
        names = {}
    cik = st.sidebar.selectbox(
        "Company",
        snapshot.available_ciks,
        index=None,
        format_func=lambda value: (
            f"{names[value]} ({value})" if value in names else value
        ),
        placeholder="Choose a company",
        key="company_cik",
    )
    _reset_dependents(
        "_company",
        cik,
        "filing_record",
        "period_label",
        "metric_record",
        "_filing",
        "_period",
    )
    if cik is None:
        st.info("Choose a company identifier to browse its filings.")
        return
    filings = _records(snapshot.filings, cik=cik)
    st.subheader("Filings")
    _overview(filings, FILING_COLUMNS)
    selected = st.selectbox(
        "Filing accession",
        range(len(filings)),
        format_func=lambda index: _filing_label(filings[index]),
        index=None,
        placeholder="Choose a filing",
        key="filing_record",
    )
    accession = None if selected is None else filings[selected]["accession_number"]
    _reset_dependents("_filing", accession, "period_label", "metric_record", "_period")
    if selected is None:
        st.info(
            "Choose a filing to inspect its financial results and quality evidence."
        )
        return
    if cik == EXAMPLE_CIK and accession == EXAMPLE_ACCESSION:
        st.info(
            "Example filing: Apple's FY2025 10-K. It reports operating cash flow "
            "for the full year only, so Gold derives Q4 as the annual value minus "
            "the Q3 year-to-date value from the previous 10-Q. The metric record "
            "below shows both inputs and the source facts behind them. Choose "
            "another company in the sidebar to explore."
        )
    _filing_details(filings[selected])
    _financial_metrics(
        _records(snapshot.financial_metrics, cik=cik, accession=accession)
    )
    _quality_issues(_records(snapshot.quality_issues, cik=cik, accession=accession))
