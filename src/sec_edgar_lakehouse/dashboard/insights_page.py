"""The Insights page: a short report on the companies in Gold, written from the data.

Every number comes from gold.company_insights or gold.fct_company_financials.
Headlines, headline numbers, takeaways and bullets are all computed from the same
rows as the charts, so they stay true when the nightly sync adds filings. Context
that the data can't show, such as a spin-off, lives in the dated written analysis. The exploratory work behind these
questions, including data coverage, is in notebooks/exploratory-analysis.ipynb.
"""

from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any, cast

import altair as alt
import streamlit as st

from sec_edgar_lakehouse.dashboard.company_page import (
    SERIES_COLORS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    Record,
    _axis,
    company_label,
    records,
)
from sec_edgar_lakehouse.dashboard.dashboard_read import InsightsSnapshot

BAR_COLOR = SERIES_COLORS[0]
# The four companies that run the large public clouds, in a fixed colour order.
CLOUD_TICKERS = ("AMZN", "MSFT", "GOOGL", "META")
REPOSITORY = (
    "https://github.com/dhirajkk91/sec-edgar-financial-data-lakehouse/blob/main"
)
ABOUT = f"""
**Companies.** S&P 500 constituents, each from its own original 10-K and 10-Q
filings. The data refreshes on weeknights as companies file.

**Years.** Fiscal years end in different months: NVIDIA's in January, Microsoft's
in June, Apple's in September. "Latest year" is each company's most recent fiscal
year with revenue.

**Definitions.** Revenue growth is the compound annual rate between the first and
latest fiscal years. Free cash flow is operating cash flow minus capital
expenditures. Cash conversion is free cash flow divided by net income, shown only
when net income is positive. A value Gold withheld is left out, never counted as zero.

**More.** The [written analysis]({REPOSITORY}/docs/analysis/what-the-data-shows.md)
has the SQL and caveats for each finding, and the
[exploratory notebook]({REPOSITORY}/notebooks/exploratory-analysis.ipynb) covers
distributions, outliers, trends and missing data.
"""


def _number(value: object) -> float | None:
    return None if value is None else float(str(value))


def pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def count_word(number: int) -> str:
    """Small counts as words, as they read in a headline: 4 -> "four"."""
    words = (
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
    )
    return words[number] if 0 <= number < len(words) else str(number)


def _join(names: Sequence[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def insight_rows(snapshot: InsightsSnapshot) -> list[Record]:
    """One plotting row per company: label, name and every insight as a float."""
    companies = {str(c["cik"]): c for c in records(snapshot.companies)}
    rows: list[Record] = []
    for insight in records(snapshot.insights):
        company = companies[str(insight["cik"])]
        first, latest = insight["first_fiscal_year"], insight["latest_fiscal_year"]
        rows.append(
            {
                "cik": str(insight["cik"]),
                "company": company_label(company),
                "name": str(company["company_name"]),
                "sector": str(company.get("sector") or "Unknown"),
                "years": f"FY{first}–FY{latest}" if first is not None else "n/a",
                "first_year": first,
                "latest_year": latest,
                "first_revenue": _number(insight["first_revenue"]),
                "latest_revenue": _number(insight["latest_revenue"]),
                "cagr": _number(insight["revenue_cagr"]),
                "net_margin": _number(insight["latest_net_margin"]),
                "fcf_margin": _number(insight["latest_free_cash_flow_margin"]),
                "conversion": _number(insight["cash_conversion"]),
                "net_income": _number(insight["latest_net_income"]),
            }
        )
    return rows


def capex_rows(
    snapshot: InsightsSnapshot, tickers: Sequence[str] = CLOUD_TICKERS
) -> list[Record]:
    """Annual capital expenditures and operating cash flow for the given tickers."""
    labels = {
        str(c["cik"]): str(c["primary_ticker"])
        for c in records(snapshot.companies)
        if c["primary_ticker"] in tickers
    }
    rows: list[Record] = []
    for row in records(snapshot.financials):
        cik = str(row["cik"])
        if cik not in labels or row["fiscal_period"] != "FY":
            continue
        capex = _number(row["capital_expenditures"])
        if capex is None:
            continue
        rows.append(
            {
                "company": labels[cik],
                "fiscal_year": int(str(row["fiscal_year"])),
                "capex": capex / 1e9,
                "operating_cash_flow": _number(row["operating_cash_flow"]),
            }
        )
    return sorted(rows, key=lambda r: (tickers.index(r["company"]), r["fiscal_year"]))


def _ranked(rows: Sequence[Mapping[str, Any]], field: str) -> list[Mapping[str, Any]]:
    return sorted((r for r in rows if r[field] is not None), key=lambda r: -r[field])


def _capex_totals(capex: Sequence[Mapping[str, Any]]) -> tuple[float, float] | None:
    """Combined capex in each company's first and latest year, in USD billions."""
    by_company: dict[str, list[Mapping[str, Any]]] = {}
    for row in capex:
        by_company.setdefault(str(row["company"]), []).append(row)
    if not by_company:
        return None
    first = sum(rows[0]["capex"] for rows in by_company.values())
    latest = sum(rows[-1]["capex"] for rows in by_company.values())
    return first, latest


def headline_numbers(
    rows: Sequence[Mapping[str, Any]], capex: Sequence[Mapping[str, Any]]
) -> list[tuple[str, str, str]]:
    """Label, value and note for each headline number the data can support."""
    tiles: list[tuple[str, str, str]] = []
    if growth := _ranked(rows, "cagr"):
        top = growth[0]
        tiles.append(
            (
                "Fastest revenue growth",
                f"{pct(top['cagr'])} a year",
                f"{top['company']}, {top['years']}",
            )
        )
    if margins := _ranked(rows, "net_margin"):
        top = margins[0]
        tiles.append(
            (
                "Highest net margin",
                pct(top["net_margin"]),
                f"{top['company']}, FY{top['latest_year']}",
            )
        )
    if conversion := _ranked(rows, "conversion"):
        converted = sum(1 for r in conversion if r["conversion"] >= 1)
        tiles.append(
            (
                "Turned all profit into cash",
                f"{converted} of {len(conversion)}",
                "companies, latest fiscal year",
            )
        )
    if totals := _capex_totals(capex):
        first, latest = totals
        names = ", ".join(dict.fromkeys(str(r["company"]) for r in capex))
        note = f"{names} · {latest / first:.1f}× their first year"
        tiles.append(("Cloud capex, latest year", f"${latest:,.0f}B", note))
    return tiles


def growth_story(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    ranked = _ranked(rows, "cagr")
    if not ranked:
        return "Revenue growth", "No company has two fiscal years of revenue yet."
    top = ranked[0]
    years = int(top["latest_year"]) - int(top["first_year"])
    multiple = top["latest_revenue"] / top["first_revenue"]
    headline = (
        f"{top['company']} grew revenue {multiple:.1f}× in {count_word(years)} "
        f"year{'' if years == 1 else 's'}, "
        f"the fastest of the {len(ranked)}"
    )
    fast = sum(1 for r in ranked if r["cagr"] > 0.2)
    bottom = ranked[-1]
    text = (
        f"The median company grew {pct(median(r['cagr'] for r in ranked))} a year, "
        f"and {fast} of {len(ranked)} grew faster than 20% a year. "
    )
    if bottom["cagr"] < 0:
        text += f"{bottom['company']} shrank {pct(-bottom['cagr'])} a year."
    else:
        text += f"{bottom['company']} grew slowest, at {pct(bottom['cagr'])} a year."
    missing = [r["company"] for r in rows if r["cagr"] is None]
    if missing:
        text += f" No rate for {_list_names(missing)}: fewer than two years of revenue."
    return headline, text


def profit_story(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    both = [
        r for r in rows if r["net_margin"] is not None and r["fcf_margin"] is not None
    ]
    if not both:
        return (
            "Profitability",
            "No company has both margins for its latest fiscal year.",
        )
    top = max(both, key=lambda r: r["net_margin"])
    cents = round(top["net_margin"] * 100)
    headline = f"{top['company']} kept {cents} cents of every revenue dollar as profit"
    above = sum(1 for r in both if r["net_margin"] > 0.3)
    below = sum(1 for r in both if r["fcf_margin"] < r["net_margin"])
    text = (
        f"{above} of {len(both)} companies had a net margin above 30%. "
        f"{below} sit below the dashed line: their free cash flow was smaller than "
        "their profit."
    )
    missing = [r["company"] for r in rows if r not in both]
    if missing:
        text += f" Not shown, missing a margin: {_list_names(missing)}."
    return headline, text


def _no_conversion_reason(row: Mapping[str, Any]) -> str:
    """Why a company has no cash conversion, in a few words."""
    if row["net_income"] is None:
        return "no net income"
    if row["net_income"] <= 0:
        return "a loss"
    return "no free cash flow"


def cash_story(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    ranked = _ranked(rows, "conversion")
    if not ranked:
        return (
            "Cash conversion",
            "No company has positive net income and free cash flow.",
        )
    converted = sum(1 for r in ranked if r["conversion"] >= 1)
    lead = "Only " if converted < len(ranked) / 2 else ""
    headline = (
        f"{lead}{converted} of {len(ranked)} companies turned all of their profit "
        "into free cash"
    )
    text = ""
    if len(ranked) > 1:
        top, bottom = ranked[0], ranked[-1]
        text = (
            f"{top['company']} converted the most ({pct(top['conversion'], 0)}) and "
            f"{bottom['company']} the least ({pct(bottom['conversion'], 0)}). "
        )
    text += (
        "Far above 100% usually means non-cash charges cut profit; far below "
        "usually means heavy capital spending."
    )
    reasons = [
        f"{r['company']} ({_no_conversion_reason(r)})"
        for r in rows
        if r["conversion"] is None
    ]
    if reasons:
        text += f" Left out: {_list_names(reasons)}."
    return headline, text


def capex_story(capex: Sequence[Mapping[str, Any]]) -> tuple[str, str] | None:
    totals = _capex_totals(capex)
    if totals is None:
        return None
    first, latest = totals
    companies = count_word(len({r["company"] for r in capex}))
    headline = (
        f"The {companies} cloud companies' capex rose {latest / first:.1f}× "
        f"to ${latest:,.0f}B a year"
    )
    latest_rows = {}
    for row in capex:
        latest_rows[row["company"]] = row
    shares = [
        (row["capex"] * 1e9 / row["operating_cash_flow"], row)
        for row in latest_rows.values()
        if row["operating_cash_flow"]
    ]
    text = f"Combined, up from ${first:,.0f}B in each company's first year here."
    if shares:
        share, row = max(shares, key=lambda item: item[0])
        text += (
            f" {row['company']} spent {pct(share, 0)} of its operating cash flow on "
            f"capex in FY{row['fiscal_year']}."
        )
    return headline, text


# Charts with one mark per company stay readable up to about this many.
CHART_LIMIT = 25
TOP_SHOWN, BOTTOM_SHOWN = 15, 5


def chart_rows(rows: Sequence[Record], field: str) -> list[Record]:
    """Every company with a value, or the top and bottom few when there are many."""
    ranked = [dict(r) for r in _ranked(rows, field)]
    if len(ranked) <= CHART_LIMIT:
        return ranked
    return ranked[:TOP_SHOWN] + ranked[-BOTTOM_SHOWN:]


def _list_names(names: Sequence[str], limit: int = 6) -> str:
    """Name a few companies and count the rest, so a long universe stays one line."""
    if len(names) <= limit:
        return _join(list(names))
    return f"{', '.join(names[:limit])} and {len(names) - limit} more"


def _money(value: float) -> str:
    """USD in the largest unit that keeps it short: $1.2T, $45.6B, $120M."""
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= size:
            return f"${value / size:,.1f}{unit}"
    return f"${value:,.0f}"


def growth_leaders(rows: Sequence[Mapping[str, Any]], count: int = 3) -> list[str]:
    """The fastest growers, with the revenue each grew from and to."""
    lines = []
    for r in _ranked(rows, "cagr")[:count]:
        verb = "grew" if r["cagr"] >= 0 else "shrank"
        lines.append(
            f"**{r['company']}** {verb} {pct(abs(r['cagr']))} a year, from "
            f"{_money(r['first_revenue'])} to {_money(r['latest_revenue'])} "
            f"({r['years']})."
        )
    return lines


def margin_leaders(rows: Sequence[Mapping[str, Any]], count: int = 3) -> list[str]:
    """The highest net margins, each beside its free cash flow margin."""
    lines = []
    for r in _ranked(rows, "net_margin")[:count]:
        if r["net_margin"] < 0:
            break
        line = (
            f"**{r['company']}** kept {pct(r['net_margin'])} of revenue as profit "
            f"in FY{r['latest_year']}"
        )
        if r["fcf_margin"] is not None:
            line += f"; its free cash flow margin was {pct(r['fcf_margin'])}"
        lines.append(line + ".")
    return lines


def conversion_extremes(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The highest and lowest cash conversion, the two ends worth a closer look."""
    ranked = _ranked(rows, "conversion")
    if len(ranked) < 2:
        return []
    ends = (ranked[0], "highest"), (ranked[-1], "lowest")
    return [
        f"**{r['company']}** had the {label} conversion, {pct(r['conversion'], 0)} "
        f"of FY{r['latest_year']} net income."
        for r, label in ends
    ]


def capex_shares(capex: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each cloud company's latest capex and its share of operating cash flow."""
    latest: dict[str, Mapping[str, Any]] = {}
    for row in capex:
        latest[str(row["company"])] = row
    lines = []
    for company, row in latest.items():
        line = f"**{company}** spent {_money(row['capex'] * 1e9)} in FY{row['fiscal_year']}"
        if row["operating_cash_flow"]:
            share = row["capex"] * 1e9 / row["operating_cash_flow"]
            line += f", {pct(share, 0)} of its operating cash flow"
        lines.append(line + ".")
    return lines


def ranked_bar(
    rows: Sequence[Record], field: str, title: str, tooltip: Sequence[alt.Tooltip]
) -> alt.Chart:
    """Single-colour horizontal bars, largest first."""
    return (
        alt.Chart(alt.Data(values=list(rows)))
        .mark_bar(size=14, cornerRadiusEnd=4, color=BAR_COLOR)
        .encode(
            y=alt.Y(
                "company:N",
                sort="-x",
                title=None,
                axis=_axis(grid=False, ticks=False, labelColor=TEXT_PRIMARY),
            ),
            x=alt.X(f"{field}:Q", title=title, axis=_axis(format=".0%")),
            tooltip=list(tooltip),
        )
        .properties(height=max(120, 26 * len(rows)))
    )


def growth_chart(rows: Sequence[Record]) -> alt.Chart:
    shown = chart_rows(rows, "cagr")
    chart = ranked_bar(
        shown,
        "cagr",
        "Revenue growth per year (compound)",
        [
            alt.Tooltip("name:N", title="Company"),
            alt.Tooltip("sector:N", title="Sector"),
            alt.Tooltip("years:N", title="Fiscal years"),
            alt.Tooltip("cagr:Q", title="Growth per year", format=".1%"),
            alt.Tooltip(
                "first_revenue:Q", title="First-year revenue ($)", format="$,.3s"
            ),
            alt.Tooltip("latest_revenue:Q", title="Latest revenue ($)", format="$,.3s"),
        ],
    )
    return chart.properties(padding={"right": 24})


def profitability_chart(rows: Sequence[Record]) -> alt.LayerChart:
    shown = [
        r for r in rows if r["net_margin"] is not None and r["fcf_margin"] is not None
    ]
    values = [float(str(r[k])) for r in shown for k in ("net_margin", "fcf_margin")]
    low, high = min(0.0, *values), max(values)
    diagonal = (
        alt.Chart(alt.Data(values=[{"x": low, "y": low}, {"x": high, "y": high}]))
        .mark_line(color=TEXT_MUTED, strokeDash=[4, 4], strokeWidth=1)
        .encode(x="x:Q", y="y:Q")
    )
    base = alt.Chart(alt.Data(values=shown))
    x = alt.X(
        "net_margin:Q", title="Net margin, latest fiscal year", axis=_axis(format=".0%")
    )
    y = alt.Y(
        "fcf_margin:Q",
        title="Free cash flow margin, latest fiscal year",
        axis=_axis(format=".0%"),
    )
    points = base.mark_circle(
        size=90, color=BAR_COLOR, opacity=1, stroke="#ffffff", strokeWidth=2
    ).encode(
        x=x,
        y=y,
        tooltip=[
            alt.Tooltip("name:N", title="Company"),
            alt.Tooltip("sector:N", title="Sector"),
            alt.Tooltip("latest_year:N", title="Fiscal year"),
            alt.Tooltip("net_margin:Q", title="Net margin", format=".1%"),
            alt.Tooltip("fcf_margin:Q", title="Free cash flow margin", format=".1%"),
        ],
    )
    labelled = shown
    if len(shown) > CHART_LIMIT:
        by_margin = sorted(shown, key=lambda r: -float(str(r["net_margin"])))
        labelled = by_margin[:8] + by_margin[-3:]
    labels = (
        alt.Chart(alt.Data(values=list(labelled)))
        .mark_text(align="left", dx=8, fontSize=11, color=TEXT_PRIMARY)
        .encode(x=x, y=y, text="company:N")
    )
    return cast(
        alt.LayerChart, alt.layer(diagonal, points, labels).properties(height=440)
    )


def conversion_chart(rows: Sequence[Record]) -> alt.LayerChart:
    shown = chart_rows(rows, "conversion")
    bars = ranked_bar(
        shown,
        "conversion",
        "Free cash flow as a share of net income, latest fiscal year",
        [
            alt.Tooltip("name:N", title="Company"),
            alt.Tooltip("sector:N", title="Sector"),
            alt.Tooltip("latest_year:N", title="Fiscal year"),
            alt.Tooltip("conversion:Q", title="Cash conversion", format=".0%"),
        ],
    )
    rule = (
        alt.Chart(alt.Data(values=[{"x": 1.0}]))
        .mark_rule(color=TEXT_MUTED, strokeDash=[4, 4])
        .encode(x="x:Q")
    )
    chart = alt.layer(bars, rule).properties(padding={"right": 24})
    return cast(alt.LayerChart, chart)


def capex_chart(capex: Sequence[Record]) -> alt.LayerChart:
    """One line per company over its fiscal years, labelled at the last point."""
    companies = list(dict.fromkeys(str(r["company"]) for r in capex))
    scale = alt.Scale(domain=companies, range=list(SERIES_COLORS[: len(companies)]))
    last = {str(r["company"]): r["fiscal_year"] for r in capex}
    data = [dict(r, is_last=r["fiscal_year"] == last[str(r["company"])]) for r in capex]
    base = alt.Chart(alt.Data(values=data))
    x = alt.X(
        "fiscal_year:O", title="Fiscal year", axis=_axis(labelAngle=0, grid=False)
    )
    y = alt.Y(
        "capex:Q",
        title="Capital expenditures, USD billions",
        axis=_axis(format="$,.0f", domain=False, ticks=False),
    )
    color = alt.Color(
        "company:N",
        scale=scale,
        legend=alt.Legend(orient="top", title=None, labelColor=TEXT_PRIMARY),
    )
    lines = base.mark_line(strokeWidth=2).encode(x=x, y=y, color=color)
    points = base.mark_circle(size=70, opacity=1).encode(
        x=x,
        y=y,
        color=color,
        tooltip=[
            alt.Tooltip("company:N", title="Company"),
            alt.Tooltip("fiscal_year:O", title="Fiscal year"),
            alt.Tooltip("capex:Q", title="Capex ($B)", format="$,.1f"),
        ],
    )
    labels = (
        base.transform_filter("datum.is_last")
        .mark_text(align="left", dx=10, fontSize=12, color=TEXT_PRIMARY)
        .encode(x=x, y=y, text="company:N")
    )
    chart = alt.layer(lines, points, labels).properties(
        height=360, padding={"right": 60, "top": 10}
    )
    return cast(alt.LayerChart, chart)


def _section(
    story: tuple[str, str],
    chart: alt.Chart | alt.LayerChart,
    drivers: Sequence[str] | None = None,
) -> None:
    headline, takeaway = story
    st.subheader(headline)
    st.altair_chart(chart, width="stretch")
    st.caption(takeaway)
    if drivers:
        st.markdown("**From the data:**\n\n" + "\n\n".join(f"- {d}" for d in drivers))


def render(snapshot: InsightsSnapshot) -> None:
    """Draw the headline numbers, one section per finding, and notes on the data."""
    st.title("What the data shows")
    all_rows = insight_rows(snapshot)
    if not all_rows:
        st.info("No company insights are available in this database yet.")
        return
    capex = capex_rows(snapshot)

    sectors = sorted(
        {str(r["sector"]) for r in all_rows if r.get("sector") not in (None, "Unknown")}
    )
    selected_sector = "All sectors"
    if len(sectors) > 1:
        selected_sector = str(
            st.selectbox(
                "Filter by sector",
                ["All sectors", *sectors],
                index=0,
                key="insights_sector",
            )
        )

    rows = (
        all_rows
        if selected_sector == "All sectors"
        else [r for r in all_rows if r.get("sector") == selected_sector]
    )
    st.caption(
        f"{len(rows)} S&P 500 companies, from their own SEC filings. "
        "Each figure is for the company's latest fiscal year unless it says otherwise."
    )
    tiles = headline_numbers(rows, capex if selected_sector == "All sectors" else [])
    for column, (label, value, note) in zip(st.columns(len(tiles)), tiles, strict=True):
        with column:
            st.metric(label, value, border=True)
            st.caption(note)

    _section(growth_story(rows), growth_chart(rows), growth_leaders(rows))
    _section(profit_story(rows), profitability_chart(rows), margin_leaders(rows))
    _section(cash_story(rows), conversion_chart(rows), conversion_extremes(rows))
    if (story := capex_story(capex)) is not None and selected_sector == "All sectors":
        _section(story, capex_chart(capex), capex_shares(capex))

    with st.expander("Table view"):
        st.dataframe(
            [
                {
                    "Company": r["company"],
                    "Name": r["name"],
                    "Sector": r.get("sector", "n/a"),
                    "Fiscal years": r["years"],
                    "Revenue growth / yr": _scaled(r["cagr"]),
                    "Net margin": _scaled(r["net_margin"]),
                    "FCF margin": _scaled(r["fcf_margin"]),
                    "Cash conversion": _scaled(r["conversion"]),
                }
                for r in rows
            ],
            hide_index=True,
            width="stretch",
            column_config={
                name: st.column_config.NumberColumn(f"{name} (%)", format="%.1f")
                for name in (
                    "Revenue growth / yr",
                    "Net margin",
                    "FCF margin",
                    "Cash conversion",
                )
            },
        )
    with st.expander("About this data"):
        st.markdown(ABOUT)


def _scaled(value: object) -> float | None:
    return None if value is None else float(str(value)) * 100
