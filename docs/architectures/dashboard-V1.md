# Dashboard: a read-only filing explorer

The dashboard is for inspecting a built dataset, not running the pipeline. Pick a CIK, then a filing accession, then an exact metric record. It shows what that filing reported, whether Gold found quality issues, and which source occurrences support a value. Filings with `PARTIAL` or `FAILED` Gold quality remain visible so missing data is not hidden by the UI.

## The boundary

The reader queries three existing Gold tables: `gold.dim_filings`, `gold.fct_financial_metrics`, and `gold.metric_quality_issues`. It opens an existing DuckDB file with `read_only=True`, reads the CIK list and all three datasets in one transaction, materializes typed immutable records, and closes the connection. The connection uses one DuckDB worker and a 4GB memory limit because these views can re-scan the cohort's Parquet files on each Streamlit rerun; insertion-order preservation stays enabled for evidence arrays. Filters are bound values, not caller-supplied SQL. It creates no tables, exports, or run records and makes no SEC requests.

The small reader layer gives the UI a testable contract independent of Streamlit. It projects known columns and checks their SQL types instead of accepting any table that happens to have the right name. It does not join metric rows to issue rows: one filing can have several of each, and a direct join would multiply both counts. Reading all three views in one transaction gives one database snapshot for the screen, while returning immutable records keeps UI filtering from changing that source snapshot.

Streamlit lives in an optional dependency group. The core package, read-only snapshot reader, and CLI inspector still work without installing the browser UI or dbt. That separation was checked with a fresh core-only wheel install, not only an editable checkout.

Metric values remain Python `Decimal`; timestamps are timezone-aware; ordered supporting-occurrence arrays keep their order, including empty and null states. The reader checks the required column names and types and returns typed empty tables for a valid selection with no rows. Filing, metric, and issue records use deterministic display ordering without selecting a preferred accession or discarding repeated issues.

The Streamlit app uses that snapshot. It gives the eight known metric codes readable labels and formats displayed Decimal values without float conversion or rounding. The full record expander still shows the exact stored decimal text, raw unit expression, status/reason codes, and current/predecessor lineage. Derived inputs are shown beside the derived value; the UI never repeats the subtraction. Changing a company, filing, or period clears dependent selections so an older row is not accidentally shown under a new filter.

The user chooses an **exact record**, not just a metric name. A filing can legitimately contain both a reported quarter and a reported year-to-date value for the same code; choosing the first row would hide that distinction. The UI keeps period boundaries, unit, and `reported`/`derived` origin visible in the table. Display formatting removes unneeded trailing fractional zeros but leaves every significant digit and integer trailing zero alone. Unknown metric or unit expressions are shown unchanged rather than guessed into a friendly label.

The selector uses CIK because the final marts do not carry a dependable company-name/ticker field. It keeps separate accessions, including amendments, instead of making an unreviewed “latest filing” choice. The displayed coverage count describes only the loaded database, not an issuer's whole filing history.

Known Gold quality statuses use success, warning, or error presentation, but the app does not recalculate status from issue counts. The expanded record retains the raw reason code. An empty missing-critical list means “None”; a null list means “Unavailable.” Those states are different, and the reader preserves them for the UI.

## Open or inspect a dataset

```sh
SEC_EDGAR_DUCKDB_PATH=data/query/sec_edgar.duckdb \
  uv run --locked --group dashboard streamlit run dashboard_app.py

uv run --locked python -m sec_edgar_lakehouse.dashboard.dashboard_inspect \
  --database data/query/sec_edgar.duckdb \
  --cik 0000320193
```

The inspector prints scope, counts, and a small financial sample; it uses the same reader and does not write a second dataset. `SEC_EDGAR_DUCKDB_PATH` can point the root Streamlit launcher at another **existing** database. Without it, the launcher follows the ignored `data/dashboard-current.duckdb` link if present, then falls back to `data/query/sec_edgar.duckdb`. A relative path is resolved from the repository root. The local link currently selects the completed 20-issuer cohort; it does not replace the older database.

Keep readers closed while a pipeline run writes to DuckDB. The transaction gives a consistent database read, but it does not turn external Parquet files and dbt's model-by-model writes into one atomic snapshot. Current Silver views also contain absolute local Parquet paths, so copying only the DuckDB file is not a deployable dashboard. Authentication, hosting, trends, ratios, and company comparisons need separate decisions and are not part of this local explorer.

Tests cover the reader's type and column contract, empty and filtered selections, exact Decimal and timestamp handling, ordered evidence arrays, and expected read errors. UI tests check selection resets and presentation against the same underlying records. The bounded [Apple/Microsoft dataset](../validation/multi-company-acceptance-V1.md) was also read without changing its database checksum. These checks support a local explorer; they do not claim a safe concurrent service while dbt is writing.

[`dashboard_read.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_read.py) owns the single read-only transaction and typed snapshot. [`dashboard_inspect.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_inspect.py) prints a small sample from that same snapshot, while [`dashboard_app.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_app.py) owns filtering and presentation. The root [launcher](../../dashboard_app.py) chooses a database path; it does not change the reader's query or Gold records. [Dashboard tests](../../tests/dashboard/) check these contracts without requiring live SEC data.
