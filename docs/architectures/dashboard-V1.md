# Dashboard: company comparison and filing explorer

The dashboard is for inspecting a built dataset, not running the pipeline. It has two views, chosen in the sidebar. **Companies**, the default, compares companies across fiscal years. **Trace a filing** follows one filing's values back to their sources: pick a company, then a filing accession, then an exact metric record. It shows what that filing reported, whether Gold found quality issues, and which source occurrences support a value. Filings with `PARTIAL` or `FAILED` Gold quality remain visible so missing data is not hidden by the UI.

## The boundary

The filing explorer's reader queries three Gold tables: `gold.dim_filings`, `gold.fct_financial_metrics`, and `gold.metric_quality_issues`. It opens an existing DuckDB file with `read_only=True`, reads the CIK list and all three datasets in one transaction, materializes typed immutable records, and closes the connection. The connection uses one DuckDB worker and a 4GB memory limit; insertion-order preservation stays enabled for evidence arrays. When Gold models were views, a full-cohort page load recomputed the whole chain and could exceed that limit; Gold is now stored as tables, so a read takes seconds. Filters are bound values, not caller-supplied SQL. It creates no tables, exports, or run records and makes no SEC requests.

The small reader layer gives the UI a testable contract independent of Streamlit. It projects known columns and checks their SQL types instead of accepting any table that happens to have the right name. It does not join metric rows to issue rows: one filing can have several of each, and a direct join would multiply both counts. Reading all three views in one transaction gives one database snapshot for the screen, while returning immutable records keeps UI filtering from changing that source snapshot.

Streamlit, and the Altair charts it bundles, live in an optional dependency group. The core package, both read-only readers, and the CLI inspector still work without installing the browser UI or dbt. Only the two UI modules, `dashboard_app` and `company_page`, need Streamlit. CI checks that separation with a fresh core-only wheel install, not only an editable checkout: it imports every other module with Streamlit absent. A local test does the same with Streamlit and Altair blocked, so a new UI module that leaks into the core is caught before CI.

Metric values remain Python `Decimal`; timestamps are timezone-aware; ordered supporting-occurrence arrays keep their order, including empty and null states. The reader checks the required column names and types and returns typed empty tables for a valid selection with no rows. Filing, metric, and issue records use deterministic display ordering without selecting a preferred accession or discarding repeated issues.

The Streamlit app uses that snapshot. It gives the eight known metric codes readable labels and formats displayed Decimal values without float conversion or rounding. The full record expander still shows the exact stored decimal text, raw unit expression, status/reason codes, and current/predecessor lineage. Derived inputs are shown beside the derived value; the UI never repeats the subtraction. Changing a company, filing, or period clears dependent selections so an older row is not accidentally shown under a new filter.

The user chooses an **exact record**, not just a metric name. A filing can legitimately contain both a reported quarter and a reported year-to-date value for the same code; choosing the first row would hide that distinction. The UI keeps period boundaries, unit, and `reported`/`derived` origin visible in the table. Display formatting removes unneeded trailing fractional zeros but leaves every significant digit and integer trailing zero alone. Unknown metric or unit expressions are shown unchanged rather than guessed into a friendly label.

The company selector shows the name from `gold.dim_companies` next to the CIK, and falls back to the bare CIK for a database without the company layer. It keeps separate accessions, including amendments, instead of making an unreviewed “latest filing” choice. The displayed coverage count describes only the loaded database, not an issuer's whole filing history.

Known Gold quality statuses use success, warning, or error presentation, but the app does not recalculate status from issue counts. The expanded record retains the raw reason code. An empty missing-critical list means “None”; a null list means “Unavailable.” Those states are different, and the reader preserves them for the UI.

## The Companies view

A second reader, `load_company_snapshot`, reads `gold.dim_companies` and `gold.fct_company_financials` in one read-only transaction with the same column and type checks. A database built before the company layer existed has neither table; the page then shows how to backfill company profiles instead of an empty chart.

Filters sit in one row above the charts: sector, up to six companies (defaulting to the five with the largest latest annual revenue), the metric, and annual or quarterly periods. Changing the sector resets the company selection. The page then shows:

- a line chart of the metric over fiscal periods, one line per company;
- a ranking of every company in scope for its latest fiscal year;
- a table of each company's latest fiscal year, side by side.

A few rules keep the charts honest:

- **Withheld values stay visible as gaps.** A missing value breaks the line instead of being bridged, and a company with no latest-year value is named under the ranking rather than silently dropped. Blank table cells are values Gold withheld.
- **Derived quarters are marked.** Points whose value comes from a derived input, such as Q4 = annual − Q3 year-to-date, are drawn hollow and labelled "Derived" in the tooltip.
- **Colours belong to companies.** The six colours are a palette checked for colour-blind separation. A company keeps its colour while it stays selected, so removing one never repaints the others; with four or fewer companies, lines are also labelled directly.
- **No new financial logic.** Charts convert Decimals to floats only for drawing. Margins and growth come from the mart; the page never recomputes a financial value.

Fiscal years end in different months, so "FY2025 Q1" covers different calendar months for Apple (September year end) and Microsoft (June year end). The chart caption says so.

## Open or inspect a dataset

```sh
SEC_EDGAR_DUCKDB_PATH=data/query/sec_edgar.duckdb \
  uv run --locked --group dashboard streamlit run dashboard_app.py

uv run --locked python -m sec_edgar_lakehouse.dashboard.dashboard_inspect \
  --database data/query/sec_edgar.duckdb \
  --cik 0000320193
```

The inspector prints scope, counts, and a small financial sample; it uses the same reader and does not write a second dataset. `SEC_EDGAR_DUCKDB_PATH` can point the root Streamlit launcher at another **existing** database. Without it, the launcher follows the ignored `data/dashboard-current.duckdb` link if present, then falls back to `data/query/sec_edgar.duckdb`. A relative path is resolved from the repository root. The local link currently selects the completed 20-issuer cohort; it does not replace the older database.

Keep readers closed while a pipeline run writes to DuckDB. The transaction gives a consistent database read, but it does not turn external Parquet files and dbt's model-by-model writes into one atomic snapshot. Current Silver views also contain absolute local Parquet paths, so copying only the DuckDB file is not a deployable dashboard. Authentication and hosting need separate decisions and are not part of this local dashboard.

Tests cover the reader's type and column contract, empty and filtered selections, exact Decimal and timestamp handling, ordered evidence arrays, and expected read errors. UI tests check selection resets and presentation against the same underlying records. The bounded [Apple/Microsoft dataset](../validation/multi-company-acceptance-V1.md) was also read without changing its database checksum. These checks support a local explorer; they do not claim a safe concurrent service while dbt is writing.

[`dashboard_read.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_read.py) owns the read-only transactions and typed snapshots for both views. [`dashboard_inspect.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_inspect.py) prints a small sample from the filing snapshot. [`dashboard_app.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_app.py) chooses the view and owns the filing explorer, while [`company_page.py`](../../src/sec_edgar_lakehouse/dashboard/company_page.py) owns the Companies view and its charts. The root [launcher](../../dashboard_app.py) chooses a database path; it does not change the reader's query or Gold records. [Dashboard tests](../../tests/dashboard/) check these contracts without requiring live SEC data.
