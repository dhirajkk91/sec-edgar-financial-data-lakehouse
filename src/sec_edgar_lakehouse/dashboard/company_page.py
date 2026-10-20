"""The Companies page: compare companies' results across fiscal years.

Every value comes from gold.fct_company_financials, which only rearranges Gold
metrics. Charts draw Decimal values as floats; the table shows the same values,
and the "Trace a filing" view shows the exact Decimals and their source facts.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, cast

import altair as alt
import streamlit as st

from sec_edgar_lakehouse.dashboard.dashboard_read import (
    CompanySnapshot,
    DashboardTable,
)

Record = dict[str, object]

# A categorical palette validated for colour-blind separation on a white surface.
# Six slots, so a chart compares at most six companies at once.
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
MAX_COMPANIES = len(SERIES_COLORS)
DEFAULT_COMPANIES = 5
TEXT_PRIMARY = "#14243e"
TEXT_MUTED = "#5b6474"
GRID = "#e6e9ee"
QUARTERS = ("Q1", "Q2", "Q3", "Q4")
UNCLASSIFIED = "Unclassified"


@dataclass(frozen=True)
class Metric:
    """A column of fct_company_financials and how to show it."""

    code: str
    label: str
    kind: Literal["usd", "per_share", "ratio"]
    inputs: tuple[str, ...]


METRICS = (
    Metric("revenue", "Revenue", "usd", ("revenue",)),
    Metric("net_income", "Net income", "usd", ("net_income",)),
    Metric(
        "free_cash_flow",
        "Free cash flow",
        "usd",
        ("operating_cash_flow", "capital_expenditures"),
    ),
    Metric(
        "operating_cash_flow",
        "Operating cash flow",
        "usd",
        ("operating_cash_flow",),
    ),
    Metric("net_margin", "Net margin", "ratio", ("net_income", "revenue")),
    Metric(
        "free_cash_flow_margin",
        "Free cash flow margin",
        "ratio",
        ("operating_cash_flow", "capital_expenditures", "revenue"),
    ),
    Metric(
        "revenue_yoy_growth", "Revenue growth vs. a year earlier", "ratio", ("revenue",)
    ),
    Metric("diluted_eps", "Diluted EPS", "per_share", ("diluted_eps",)),
)
METRIC_BY_CODE = {metric.code: metric for metric in METRICS}
AXIS_TITLES = {"usd": "USD billions", "per_share": "USD per share", "ratio": "Percent"}


def records(table: DashboardTable) -> list[Record]:
    """Turn a loaded table into dictionaries without changing any value."""
    return [dict(zip(table.columns, row, strict=True)) for row in table.rows]


def company_label(company: Mapping[str, object]) -> str:
    """Short name for charts: the ticker, or the SEC name when the SEC lists no ticker."""
    ticker = company.get("primary_ticker")
    return str(ticker) if ticker else str(company["company_name"])


def sector_of(company: Mapping[str, object]) -> str:
    sector = company.get("sector")
    return str(sector) if sector else UNCLASSIFIED


def plot_value(value: object, kind: str) -> float | None:
    """Scale a stored value for drawing: USD in billions, ratios and EPS unchanged."""
    if value is None:
        return None
    number = float(value) if isinstance(value, (Decimal, int, float)) else None
    if number is None:
        return None
    return number / 1e9 if kind == "usd" else number


def format_value(value: object, kind: str) -> str:
    """Readable text for tooltips: $416.2B, $7.46, 26.9%. Missing stays visible."""
    number = plot_value(value, kind)
    if number is None:
        return "Not available"
    if kind == "usd":
        return f"-${abs(number):,.1f}B" if number < 0 else f"${number:,.1f}B"
    if kind == "per_share":
        return f"-${abs(number):,.2f}" if number < 0 else f"${number:,.2f}"
    return f"{number * 100:.1f}%"


def period_order(fiscal_year: int, fiscal_period: str) -> tuple[int, int]:
    """Sort FY rows by year, and quarters Q1 to Q4 within a year."""
    index = QUARTERS.index(fiscal_period) + 1 if fiscal_period in QUARTERS else 0
    return fiscal_year, index


def period_label(fiscal_year: int, fiscal_period: str) -> str:
    return (
        f"FY{fiscal_year}"
        if fiscal_period == "FY"
        else f"FY{fiscal_year} {fiscal_period}"
    )


def companies_by_latest_revenue(
    companies: Sequence[Record], financials: Sequence[Record]
) -> list[str]:
    """CIKs ordered by their latest annual revenue, largest first; unknown revenue last."""
    latest: dict[str, tuple[int, float]] = {}
    for row in financials:
        if row["fiscal_period"] != "FY" or row["revenue"] is None:
            continue
        cik = str(row["cik"])
        year = int(str(row["fiscal_year"]))
        if cik not in latest or year > latest[cik][0]:
            latest[cik] = (year, float(str(row["revenue"])))
    return [
        str(company["cik"])
        for company in sorted(
            companies,
            key=lambda c: (
                -latest[str(c["cik"])][1] if str(c["cik"]) in latest else float("inf"),
                str(c["company_name"]),
            ),
        )
    ]


def assign_colors(
    selected: Sequence[str], previous: Mapping[str, str]
) -> dict[str, str]:
    """Give each selected company a stable colour.

    A company keeps its colour while it stays selected, so removing one company
    never repaints the others. A newly added company takes the first free slot.
    """
    colors = {cik: previous[cik] for cik in selected if cik in previous}
    free = [color for color in SERIES_COLORS if color not in colors.values()]
    for cik in selected:
        if cik not in colors and free:
            colors[cik] = free.pop(0)
    return colors


def series_rows(
    financials: Sequence[Record],
    labels: Mapping[str, str],
    selected: Sequence[str],
    metric: Metric,
    periods: Literal["Annual", "Quarterly"],
) -> list[Record]:
    """One chart point per company and period that has a value.

    A missing value breaks the line (a new segment starts) instead of being
    bridged, so a withheld number never looks like a straight-line trend.
    """
    wanted = {"FY"} if periods == "Annual" else set(QUARTERS)
    rows = sorted(
        (
            row
            for row in financials
            if row["cik"] in selected and row["fiscal_period"] in wanted
        ),
        key=lambda row: (
            str(row["cik"]),
            period_order(int(str(row["fiscal_year"])), str(row["fiscal_period"])),
        ),
    )
    result: list[Record] = []
    segment: dict[str, int] = {}
    for row in rows:
        cik = str(row["cik"])
        value = plot_value(row[metric.code], metric.kind)
        if value is None:
            segment[cik] = segment.get(cik, 0) + 1
            continue
        year, fiscal_period = int(str(row["fiscal_year"])), str(row["fiscal_period"])
        derived_codes = row["derived_metric_codes"] or ()
        derived = any(code in derived_codes for code in metric.inputs)  # type: ignore[operator]
        result.append(
            {
                "cik": cik,
                "company": labels[cik],
                "period": period_label(year, fiscal_period),
                "order": year * 10 + period_order(year, fiscal_period)[1],
                "value": value,
                "display": format_value(row[metric.code], metric.kind),
                "origin": "Derived from cumulative filings" if derived else "Reported",
                "derived": derived,
                "line": f"{cik}-{segment.get(cik, 0)}",
                "is_last": False,
            }
        )
    last: dict[str, Record] = {}
    for point in result:
        last[str(point["cik"])] = point
    for point in last.values():
        point["is_last"] = True
    return result


def _percent(value: object) -> float | None:
    """A stored ratio as a percentage number for the table; missing stays None."""
    return None if value is None else float(str(value)) * 100


def latest_annual_rows(
    financials: Sequence[Record], companies: Sequence[Record], ciks: Sequence[str]
) -> list[Record]:
    """Each company's most recent fiscal year, for the comparison table."""
    by_cik = {str(company["cik"]): company for company in companies}
    latest: dict[str, Record] = {}
    for row in financials:
        cik = str(row["cik"])
        if row["fiscal_period"] != "FY" or cik not in ciks:
            continue
        if cik not in latest or int(str(row["fiscal_year"])) > int(
            str(latest[cik]["fiscal_year"])
        ):
            latest[cik] = row
    table = []
    for cik in ciks:
        if cik not in latest:
            continue
        row, company = latest[cik], by_cik[cik]
        table.append(
            {
                "Company": company["company_name"],
                "Ticker": company["primary_ticker"] or "—",
                "Sector": sector_of(company),
                "Fiscal year": row["fiscal_year"],
                "Revenue": plot_value(row["revenue"], "usd"),
                "Net income": plot_value(row["net_income"], "usd"),
                "Free cash flow": plot_value(row["free_cash_flow"], "usd"),
                "Net margin": _percent(row["net_margin"]),
                "Revenue growth": _percent(row["revenue_yoy_growth"]),
                "Diluted EPS": plot_value(row["diluted_eps"], "per_share"),
                "Industry (SIC)": company["industry"],
            }
        )
    return table


def _axis(**kwargs: Any) -> alt.Axis:
    """Recessive axis styling; any setting passed in overrides the default."""
    settings: dict[str, Any] = {
        "labelColor": TEXT_MUTED,
        "titleColor": TEXT_MUTED,
        "gridColor": GRID,
        "domainColor": GRID,
        "tickColor": GRID,
    }
    settings.update(kwargs)
    return alt.Axis(**settings)


def trend_chart(
    rows: Sequence[Record],
    metric: Metric,
    colors: Mapping[str, str],
    labels: Mapping[str, str],
) -> alt.LayerChart:
    """Lines per company; hollow points mark derived values; tooltips on every point."""
    selected = list(colors)
    scale = alt.Scale(
        domain=[labels[cik] for cik in selected],
        range=[colors[cik] for cik in selected],
    )
    order = [
        label
        for _, label in sorted({(int(str(r["order"])), str(r["period"])) for r in rows})
    ]
    value_format = {"usd": "$,.0f", "per_share": "$,.2f", "ratio": ".0%"}[metric.kind]
    data = alt.Data(values=list(rows))
    x = alt.X(
        "period:N",
        sort=order,
        title=None,
        axis=_axis(labelAngle=0 if len(order) <= 8 else -45, grid=False),
    )
    y = alt.Y(
        "value:Q",
        title=AXIS_TITLES[metric.kind],
        axis=_axis(format=value_format, domain=False, ticks=False),
    )
    color = alt.Color(
        "company:N",
        scale=scale,
        legend=alt.Legend(orient="top", title=None, labelColor=TEXT_PRIMARY),
    )
    tooltip = [
        alt.Tooltip("company:N", title="Company"),
        alt.Tooltip("period:N", title="Period"),
        alt.Tooltip("display:N", title=metric.label),
        alt.Tooltip("origin:N", title="Origin"),
    ]
    base = alt.Chart(data)
    lines = base.mark_line(strokeWidth=2).encode(x=x, y=y, color=color, detail="line:N")
    points = base.mark_point(size=70, strokeWidth=2, opacity=1).encode(
        x=x,
        y=y,
        color=color,
        fill=alt.condition(
            "datum.derived",
            alt.value("#ffffff"),
            alt.Fill("company:N", scale=scale, legend=None),
        ),
    )
    # Invisible larger marks give an easy hover target for the tooltip.
    hover = base.mark_point(size=500, opacity=0).encode(x=x, y=y, tooltip=tooltip)
    layers: list[alt.Chart] = [lines, points, hover]
    if len(selected) <= 4:
        labels_layer = (
            base.transform_filter("datum.is_last")
            .mark_text(align="left", dx=10, fontSize=12, color=TEXT_PRIMARY)
            .encode(x=x, y=y, text="company:N")
        )
        layers.append(labels_layer)
    chart = alt.layer(*layers).properties(height=380, padding={"right": 70, "top": 10})
    return cast(alt.LayerChart, chart)


def ranking_chart(rows: Sequence[Record], metric: Metric) -> alt.Chart:
    """Latest fiscal year for every company in scope, one colour, largest first."""
    data = alt.Data(values=list(rows))
    value_format = {"usd": "$,.0f", "per_share": "$,.2f", "ratio": ".0%"}[metric.kind]
    return (
        alt.Chart(data)
        .mark_bar(size=14, cornerRadiusEnd=4, color=SERIES_COLORS[0])
        .encode(
            y=alt.Y(
                "company:N",
                sort="-x",
                title=None,
                axis=_axis(grid=False, ticks=False, labelColor=TEXT_PRIMARY),
            ),
            x=alt.X(
                "value:Q",
                title=AXIS_TITLES[metric.kind],
                axis=_axis(format=value_format),
            ),
            tooltip=[
                alt.Tooltip("company:N", title="Company"),
                alt.Tooltip("period:N", title="Fiscal year"),
                alt.Tooltip("display:N", title=metric.label),
            ],
        )
        .properties(height=max(120, 26 * len(rows)))
    )


def _reset_on_change(scope: str, value: object, *keys: str) -> None:
    if st.session_state.get(scope) != value:
        for key in keys:
            st.session_state.pop(key, None)
    st.session_state[scope] = value


def render(snapshot: CompanySnapshot) -> None:
    """Draw the filters, the trend chart, the latest-year ranking and the table."""
    companies = records(snapshot.companies)
    financials = records(snapshot.financials)
    st.title("Company financials from SEC filings")
    if not companies or not financials:
        st.info("No company financials are available in this database yet.")
        return
    years = [int(str(row["fiscal_year"])) for row in financials]
    st.caption(
        f"{len(companies)} companies · fiscal years {min(years)}–{max(years)} · "
        "every value comes from a 10-K or 10-Q XBRL fact. Use “Trace a filing” in "
        "the sidebar to follow any number back to its source."
    )
    labels = {str(c["cik"]): company_label(c) for c in companies}
    names = {str(c["cik"]): str(c["company_name"]) for c in companies}

    filters = st.columns([2, 3, 2, 1.4])
    sectors = sorted({sector_of(c) for c in companies})
    chosen_sectors = filters[0].multiselect(
        "Sector", sectors, key="sector_filter", placeholder="All sectors"
    )
    in_scope = [
        c for c in companies if not chosen_sectors or sector_of(c) in chosen_sectors
    ]
    ordered = companies_by_latest_revenue(in_scope, financials)
    _reset_on_change("_sector_scope", tuple(chosen_sectors), "company_filter")
    if "company_filter" not in st.session_state:
        st.session_state["company_filter"] = ordered[:DEFAULT_COMPANIES]
    selected = filters[1].multiselect(
        f"Companies (up to {MAX_COMPANIES})",
        ordered,
        format_func=lambda cik: f"{labels[cik]} · {names[cik]}",
        max_selections=MAX_COMPANIES,
        key="company_filter",
    )
    metric = METRIC_BY_CODE[
        filters[2].selectbox(
            "Metric",
            [m.code for m in METRICS],
            format_func=lambda code: METRIC_BY_CODE[code].label,
            key="metric_choice",
        )
    ]
    periods = cast(
        Literal["Annual", "Quarterly"],
        filters[3].radio(
            "Periods", ("Annual", "Quarterly"), key="period_choice", horizontal=True
        ),
    )
    colors = assign_colors(selected, st.session_state.get("_company_colors", {}))
    st.session_state["_company_colors"] = colors

    st.subheader(f"{metric.label}, {periods.lower()}")
    rows = series_rows(financials, labels, selected, metric, periods)
    if not selected:
        st.info("Choose at least one company to draw the trend.")
    elif not rows:
        st.info(f"No {metric.label.lower()} values are available for these companies.")
    else:
        st.altair_chart(trend_chart(rows, metric, colors, labels), width="stretch")
        st.caption(
            "Hollow points are derived quarters, such as Q4 = annual − Q3 year-to-date. "
            "A gap in a line is a value Gold withheld, never an estimate. Q4 EPS is never derived. "
            "Fiscal years end in different months (Apple's in September, Microsoft's in June), "
            "so the same label can cover different calendar months."
        )

    latest = latest_annual_rows(financials, in_scope, ordered)
    ranking = []
    for cik in ordered:
        annual = [
            r for r in financials if r["cik"] == cik and r["fiscal_period"] == "FY"
        ]
        if not annual:
            continue
        newest = max(annual, key=lambda r: int(str(r["fiscal_year"])))
        value = plot_value(newest[metric.code], metric.kind)
        if value is not None:
            ranking.append(
                {
                    "company": labels[cik],
                    "period": f"FY{newest['fiscal_year']}",
                    "value": value,
                    "display": format_value(newest[metric.code], metric.kind),
                }
            )
    st.subheader(f"{metric.label}, latest fiscal year")
    shown = {str(row["company"]) for row in ranking}
    withheld = [labels[cik] for cik in ordered if labels[cik] not in shown]
    if ranking:
        st.altair_chart(ranking_chart(ranking, metric), width="stretch")
        if withheld:
            st.caption(
                f"No latest-year value for {', '.join(withheld)}: "
                "Gold withheld it, so the company is left out of this ranking."
            )
    else:
        st.info(f"No {metric.label.lower()} values for the latest fiscal year.")

    st.subheader("Latest fiscal year, side by side")
    st.dataframe(
        latest,
        hide_index=True,
        width="stretch",
        column_config={
            "Fiscal year": st.column_config.NumberColumn(format="%d"),
            "Revenue": st.column_config.NumberColumn("Revenue ($B)", format="%.1f"),
            "Net income": st.column_config.NumberColumn(
                "Net income ($B)", format="%.1f"
            ),
            "Free cash flow": st.column_config.NumberColumn(
                "Free cash flow ($B)", format="%.1f"
            ),
            "Net margin": st.column_config.NumberColumn(
                "Net margin (%)", format="%.1f"
            ),
            "Revenue growth": st.column_config.NumberColumn(
                "Revenue growth (%)", format="%.1f"
            ),
            "Diluted EPS": st.column_config.NumberColumn(
                "Diluted EPS ($)", format="%.2f"
            ),
        },
    )
    st.caption(
        "Blank cells are values Gold withheld because the filing's evidence was missing, "
        "conflicting or unsupported; each one is recorded in gold.metric_quality_issues. "
        "Sector is the SEC's own industry office, not a market classification such as GICS."
    )
