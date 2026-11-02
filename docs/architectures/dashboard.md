# Dashboard: company comparison, insights and filing explorer

## Purpose

The dashboard is a read-only Streamlit app over the Gold tables. It has three views: **Companies** compares companies across fiscal years, **Insights** is a short report on what the companies' numbers show, and **Trace a filing** follows any value back to the filing, period and XBRL facts behind it. It shows what Gold stored and never recalculates a financial value, so what's on screen is exactly what the tests checked.

It runs locally against a built database, and publicly at [sec-lakehouse.streamlit.app](https://sec-lakehouse.streamlit.app/) against a small copy of Gold (see [Hosted dashboard](#hosted-dashboard)).

## Goals and non-goals

Goals:

- Read Gold through a typed, tested contract, independent of Streamlit.
- Show every value exactly: exact decimals, the full lineage, and both inputs of a derived value.
- Keep gaps visible: a withheld value is a gap with a reason, never a bridged line or a silent omission.
- Let the core package work without the UI libraries installed.

Non-goals:

- Writing to the database, running the pipeline or calling the SEC.
- Authentication. Everything shown is public SEC data.
- Serving reads while the pipeline is writing.

## Design

```mermaid
flowchart LR
    DB[("DuckDB<br/>gold.* tables")] --> R1["load_dashboard_snapshot<br/>filings, metrics, issues"]
    DB --> R2["load_company_snapshot<br/>companies, company financials"]
    DB --> R3["load_insights_snapshot<br/>companies, insights, company financials"]
    R1 --> S1["Typed, immutable snapshot"]
    R2 --> S2["Typed, immutable snapshot"]
    R3 --> S3["Typed, immutable snapshot"]
    S1 --> T["Trace a filing<br/>dashboard_app.py"]
    S2 --> C["Companies<br/>company_page.py"]
    S3 --> N["Insights<br/>insights_page.py"]
    L["dashboard_app.py launcher<br/>or deploy/streamlit_app.py"] -. "chooses the database" .-> DB
```

### The reader

[`dashboard_read.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_read.py) is the only code that queries the database. Each snapshot is one read-only DuckDB connection and one transaction, so a page never mixes two refreshes. It then closes the connection and returns immutable records.

- **A checked contract.** The reader selects known columns and checks their SQL types, instead of accepting any table that happens to have the right name. A missing or incompatible table is an error on the page, not an empty result.
- **Exact values.** Metric values stay Python `Decimal`, timestamps stay timezone-aware, and evidence arrays keep their order, including the difference between an empty list and a null.
- **No accidental joins.** Metrics and issues are returned separately. A filing can have several of each, and joining them would multiply both counts.
- **Bounded resources.** One DuckDB thread and a 4 GB memory limit, with insertion order preserved for evidence arrays. Filters are bound parameters, never SQL text from the page.

The UI depends on these snapshots, not on DuckDB, which keeps it testable with small fixture databases.

### Companies

The default view. Filters sit in one row: sector, up to six companies (by default the five with the largest latest annual revenue), a metric, and annual or quarterly periods. Changing the sector resets the selection. Below them are a line chart over fiscal periods, a ranking of every company in scope for its latest fiscal year, and a side-by-side table of the latest year.

A few rules keep the charts honest:

- **Gaps stay gaps.** A withheld value breaks the line instead of being bridged, and a company with no latest-year value is named under the ranking rather than dropped. Blank table cells are values Gold withheld.
- **Derived values are marked.** Points that come from a derived quarter, such as Q4 = annual − Q3 year to date, are hollow and labelled in the tooltip.
- **Colours belong to companies.** Six colours from a palette checked for colour-blind separation. A company keeps its colour while it stays selected, so removing one never repaints the others; with four or fewer companies, lines are labelled directly.
- **No new financial logic.** Decimals become floats only for drawing. Margins and growth come from the mart.

Fiscal years end in different months, so "FY2025 Q1" means different calendar months for Apple (September year end) and Microsoft (June). The chart caption says so. A database without the company tables shows how to load them instead of an empty chart.

### Insights

A short report: four headline numbers, then one section per finding (revenue growth, profitability, cash conversion and the cloud companies' capital spending), each with a chart and a two-sentence takeaway. The figures come from [`company_insights`](gold-financial-metrics.md#outputs) and `fct_company_financials`; the page only formats them. Definitions and scope sit in an "About this data" note at the bottom, and a table view repeats every figure.

- **Everything on the page is computed.** Each section heading states its finding ("NVDA grew revenue 8.0× in four years, the fastest of the 20"), and the headline numbers, takeaways and bullets come from the same rows as the charts. The page never hard-codes a number, so it stays true when the weeknight sync adds filings. Context the data can't show, such as a spin-off, belongs in the dated [written analysis](../analysis/what-the-data-shows.md).
- **Built for 500 companies.** A ranking with more than 25 companies shows the top 15 and bottom 5, the scatter labels only its ends, and lists of left-out companies name a few and count the rest.
- **Left out means named.** A company without a growth rate, a margin or a cash conversion is listed under the chart with the reason (a loss, no free cash flow, fewer than two years), not silently dropped.
- **One colour per job.** The ranked bars and the margin scatter use a single blue with tickers labelled directly; the capex chart gives each of its four companies a fixed colour and labels the lines.

Data coverage and the exploration behind these questions live in the [exploratory notebook](../../notebooks/exploratory-analysis.ipynb), not on the page.

A database built before these tables existed shows a rebuild hint instead of an empty page.

### Trace a filing

The user picks a company, a filing and then an **exact metric record**, not just a metric name. A 10-Q can contain both a reported quarter and a reported year to date for the same metric, and picking "the revenue row" would hide that difference.

```mermaid
flowchart TD
    A["Company<br/>name from dim_companies + CIK"] --> B["Filing<br/>accession, form, report date"]
    B --> Q["Quality status<br/>and issue counts"]
    B --> C["Metric record<br/>exact period, unit, reported or derived"]
    C --> V["Exact value"]
    C --> D["Both inputs and method<br/>(derived values)"]
    C --> J["Full record: occurrence IDs,<br/>accessions, documents, checksums"]
    B --> I["Quality issues for this filing"]
```

- **It opens on an example.** When the database contains Apple's derived FY2025 Q4 operating cash flow, the view opens on that record with a short note on how it was derived. Clearing the company keeps it cleared until the view is opened again; a database without that record opens empty.
- **Nothing is hidden.** `PARTIAL` and `FAILED` filings stay listed. Each filing keeps its own accession, amendments included, with no "latest filing" choice made for the user.
- **Status comes from Gold.** Quality statuses get success, warning or error styling, but the page never recomputes a status from issue counts. An empty missing-metrics list shows "None"; a null shows "Unavailable".
- **Formatting is display-only.** Values get commas and lose trailing fractional zeros, but no digit is rounded and nothing goes through a float. The full record shows the stored decimal text, raw unit and every lineage field. Unknown metric codes and units are shown as stored, not guessed.
- **Selections can't go stale.** Changing the company, filing or period clears the selections that depend on it, so an old row never appears under a new filter.

### Optional UI dependencies

Streamlit and Altair are in an optional dependency group. The core package, the readers and the demo export work without them; only `dashboard_app`, `company_page` and `insights_page` import Streamlit. CI installs the built wheel without the UI group and imports every other module, and a local test blocks Streamlit and Altair to catch a leak before CI does.

## Running it locally

```sh
make dashboard                                        # the local cohort database
make dashboard DATABASE=data/lake/query/sec_edgar.duckdb   # the synced S&P 500
uv run python duckdb_startup.py                             # DuckDB's SQL UI over the same file
```

The [launcher](../../dashboard_app.py) resolves the database in this order: `SEC_EDGAR_DUCKDB_PATH`, then the ignored `data/dashboard-current.duckdb` link if it exists, then `data/query/sec_edgar.duckdb`. Relative paths resolve from the repository root.

Keep the dashboard closed while the pipeline writes. A read transaction gives a consistent snapshot of the database, but it can't make dbt's model-by-model writes or the external Parquet files atomic.

## Hosted dashboard

The public app shows the live S&P 500 data when it can and the committed demo when it can't:

```mermaid
flowchart LR
    J["Weeknight job<br/>publish-lake"] --> S3[("S3<br/>serving/sec_edgar_gold.duckdb<br/>+ manifest")]
    S3 -->|"read-only IAM key,<br/>cached 1 h"| H["deploy/streamlit_app.py<br/>Streamlit Community Cloud"]
    H -->|"checksum matches?"| V["Live data"]
    D["demo/sec_edgar_demo.duckdb<br/>20 companies, Oct 2026"] -->|"any failure, or no secret set"| H
```

With `SEC_EDGAR_SNAPSHOT_S3_URI` and a read-only AWS key in the app's Streamlit secrets, [`deploy/streamlit_app.py`](../../deploy/streamlit_app.py) downloads the snapshot the weeknight job published, checks its SHA-256 against the manifest beside it and keeps it for an hour, so new batches from a backfill appear within the hour. Any failure falls back to the committed demo, so the page always opens. The sidebar says which one is showing and when it was published. The IAM key can read exactly those two objects and nothing else ([Terraform](../../infra/aws/main.tf)).

### The Gold-only copy

The local database can't be published as it is: its Silver views point to Parquet files by absolute path on the build machine, and `dim_filings` stores those paths too. The dashboard reads only Gold, so the public app reads a small copy of just the Gold tables.

```mermaid
flowchart LR
    A[("Cohort database")] --> B["sec-edgar export-demo"]
    B --> C{"Checks pass?"}
    C -- no --> X["Nothing written"]
    C -- yes --> D["demo/sec_edgar_demo.duckdb<br/>+ manifest with SHA-256"]
    D --> G["GitHub"]
    G --> H["Streamlit Community Cloud<br/>deploy/streamlit_app.py"]
    G --> K["Docker image"]
```

[`demo_export.py`](../../src/sec_edgar_lakehouse/dashboard/demo_export.py) copies exactly the seven Gold tables the dashboard and the exploratory notebook read, and:

- refuses a source whose Gold models are views, since a view would still need the Silver files;
- rewrites `dim_filings.version_path` and `publication_path` from absolute paths to paths relative to the data folder, such as `silver/cik=…`;
- refuses to finish if any text value still looks like a home, temp or drive path, contains an email address, or contains the data folder's own path;
- compares every row count with the source and opens the result with all three dashboard readers;
- builds under a temporary name and moves the file into place only after all checks pass;
- writes `demo/sec_edgar_demo.json` with the file's SHA-256 and row counts.

```sh
make demo             # rebuild from the local cohort link
make demo-dashboard   # open it through the hosted entry point
```

The committed file is about 3 MB: 400 filings, 4,310 financial values, 557 issues, 20 companies, 500 company-period rows, and the 20 insight and 160 coverage rows. It is the frozen 20-company snapshot that the notebook and written analysis describe. The weeknight job runs the same export over the S&P 500 database, so the live snapshot passes the same checks.

**Hosting.** Streamlit Community Cloud clones the repository, installs [`deploy/requirements.txt`](../../deploy/requirements.txt) and runs [`deploy/streamlit_app.py`](../../deploy/streamlit_app.py) on Python 3.14. The entry point adds `src/` to the import path instead of installing the package, so the host only needs the pinned packages. It opens the live snapshot or the demo file (or `SEC_EDGAR_DUCKDB_PATH`), then calls the same `main` as the local launcher: no reader, query or chart code differs between the two.

**Checks.** A separate CI job installs only `deploy/requirements.txt` and pytest on Python 3.12, the oldest version the source supports, and runs [the hosted-demo tests](../../tests/dashboard/test_hosted_demo.py): the committed file must match its manifest and contain only the seven Gold tables, every pin must match `uv.lock`, a downloaded snapshot must match its manifest's checksum, and all three views must open without errors. After a dependency change, the pins are regenerated from `uv.lock` with the `uv export` command written at the top of the requirements file. Ruff's target version keeps the source free of syntax newer than 3.12.

### Run it in Docker

The [`Dockerfile`](../../Dockerfile) packages the same hosted app: `python:3.14-slim`, the packages from `deploy/requirements.txt`, then only `src/`, the entry point, the demo database and the Streamlit theme. It runs as a non-root user with a health check on Streamlit's `/_stcore/health`, and [`.dockerignore`](../../.dockerignore) keeps `data/` and other large folders out of the build context.

```sh
docker build --tag sec-edgar-dashboard .
docker run --rm --publish 8501:8501 sec-edgar-dashboard
```

`make docker-run` does both. The image doesn't run the pipeline: it makes no SEC requests and contains no dbt project or Silver files. A CI job builds it, starts it and waits for the health check.

The demo has no Silver facts, so the `silver.facts` queries in the [pipeline guide](../guides/running-the-pipeline.md#inspect-the-results) only work on a local build ([decision 16](../decisions/engineering-decisions.md#16-the-live-demo-reads-a-gold-only-copy-not-the-pipelines-database)).

## Trade-offs and limits

| Choice | Cost |
| --- | --- |
| Read the whole snapshot per page load, then filter in memory | Fine for 400 filings; a much larger dataset would need filtered queries |
| One read-only connection, closed after each read | Reads must not overlap pipeline writes |
| The hosted app reads a Gold-only snapshot | It must be re-exported after each rebuild, and can't show raw Silver facts |
| Streamlit | Quick to build and easy to host, but limited layout control |

## Code and tests

| Part | Code |
| --- | --- |
| Readers and snapshot types | [`dashboard_read.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_read.py) |
| View selection and the filing explorer | [`dashboard_app.py`](../../src/sec_edgar_lakehouse/dashboard/dashboard_app.py) |
| Companies view and charts | [`company_page.py`](../../src/sec_edgar_lakehouse/dashboard/company_page.py) |
| Insights view, charts and headlines | [`insights_page.py`](../../src/sec_edgar_lakehouse/dashboard/insights_page.py) |
| Hosted entry point and live snapshot | [`deploy/streamlit_app.py`](../../deploy/streamlit_app.py) |
| Demo export | [`demo_export.py`](../../src/sec_edgar_lakehouse/dashboard/demo_export.py) |
| Local launcher, hosted entry point | [`dashboard_app.py`](../../dashboard_app.py), [`deploy/streamlit_app.py`](../../deploy/streamlit_app.py) |

The [dashboard tests](../../tests/dashboard/) cover the reader's column and type contract, empty and filtered selections, exact decimals and timestamps, ordered evidence, read errors, selection resets, the example trace, every Insights headline and takeaway, and the demo export's path and email checks. The [Apple and Microsoft run](../validation/multi-company-acceptance.md) read a real database without changing its checksum.
