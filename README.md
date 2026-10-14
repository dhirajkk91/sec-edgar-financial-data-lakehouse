# SEC EDGAR Financial Data Lakehouse

This project takes SEC filings from source documents to financial values you can inspect in SQL or a local dashboard. It downloads and verifies the filing, extracts numeric XBRL facts, selects a small set of reported metrics, and keeps the evidence behind each number. When the source is incomplete or ambiguous, it records the problem instead of filling a gap with a guess.

It runs locally with Python, Parquet, DuckDB, dbt, and Streamlit. I built it around one question: **if a number shows up in the dashboard, can I get back to the filing and the exact fact that produced it?**

```text
SEC submissions + filing index
           │
           ▼
Bronze: verified source files and manifests
           │
           ▼
Silver: XBRL occurrences, rejections, versioned Parquet
           │
           ▼
DuckDB: active Silver catalog + verified filing/fiscal metadata
           │
           ▼
dbt Gold: selected metrics, filing summaries, quality issues
           │
           ▼
Read-only Streamlit filing explorer
```

The key choices are deliberate: keep the SEC bytes before interpreting them, keep every XBRL occurrence before selecting a metric, and publish a verified Silver version before changing the active pointer. DuckDB exposes only active versions; dbt owns the financial decisions; the dashboard reads the result without rewriting it. Each layer has its own verification and publication step, so a failed dbt build does not erase an earlier verified source file.

The identities stay separate too. **CIK + accession** names a filing; the **document name + checksum** names the bytes parsed; a **source occurrence ID** names one fact in those bytes; a **Silver version** also includes parser/schema versions; a **run ID** names an execution, even when it reuses existing data. Gold then selects a metric for one accession and exact period. Keeping those keys distinct is what makes reruns and source lineage inspectable.

## What it currently covers

Gold selects eight metrics: revenue, net income, diluted EPS, total assets, total liabilities, cash and equivalents, operating cash flow, and capital expenditures. A value carries its exact period, unit, source accession, and supporting XBRL occurrence IDs. Reported annual, quarter, year-to-date, and instant values stay separate. The supported derived quarters are cash flow/capex from compatible cumulative filings and Q4 revenue, net income, cash flow, and capex from annual minus Q3 year-to-date. EPS is never derived by subtraction.

The three final Gold views are `gold.fct_financial_metrics`, `gold.dim_filings`, and `gold.metric_quality_issues`. A successful pipeline run can still have financial warnings. For example, a missing earlier 10-Q can prevent a standalone quarter from being calculated while the reported year-to-date value remains useful.

One result shows the handoff end to end. Apple's FY 2025 filing reports 111,482,000,000 USD in operating cash flow; its Q3 filing reports 81,754,000,000 USD year to date. Bronze keeps both checked XML files, Silver assigns stable IDs to the two occurrences, and the catalog exposes their active versions. Gold subtracts the compatible Q3 value to produce 29,728,000,000 USD for Q4 and keeps both accessions and occurrence IDs on the derived row. Without that Q3 filing, the annual value would still be available, but Q4 would be a warning rather than a guessed number.

The [two-company acceptance run](docs/validation/multi-company-acceptance-V1.md) used two 2025 filings each for Apple and Microsoft in one local database. It produced 39 financial rows across four filings and six derivation warnings. A Microsoft rerun reused its published inputs and left the complete ordered Gold records unchanged. Those counts describe that bounded dataset, not expected counts for other companies.

## Run it locally

Use Python 3.14 and [uv](https://docs.astral.sh/uv/getting-started/installation/). From the repository root, install the locked dependencies needed for the full pipeline and dashboard:

```sh
uv sync --locked --group dbt --group dashboard
```

SEC requests need an identified `SEC_USER_AGENT` in your environment. Use your own application name and contact address; keep the value out of tracked files. The end-to-end CLI processes **one company per invocation**. This example selects up to two Apple 10-K/10-Q filings filed in 2025 and writes to ignored `data/demo/` paths:

```sh
export SEC_USER_AGENT="Your App Name your.email@example.com"
mkdir -p data/demo/runs/dbt
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.end_to_end_pipeline_run \
  --cik 0000320193 --forms 10-K 10-Q \
  --filed-on-or-after 2025-01-01 --filed-on-or-before 2025-12-31 --limit 2 \
  --bronze-directory data/demo/bronze/sec/filings \
  --silver-directory data/demo/silver/sec/filings \
  --database data/demo/query/sec_edgar.duckdb \
  --run-directory data/demo/runs/company \
  --end-to-end-run-directory data/demo/runs/end-to-end \
  --dbt-project-directory transform --dbt-profiles-directory transform \
  --dbt-artifact-directory data/demo/runs/dbt/apple-first
```

The command prints stage outcomes and paths to its saved summary and dbt artifacts. A rerun needs a **new** run ID and a fresh `--dbt-artifact-directory`; add `--run-id` if you want to name each attempt yourself. The same Bronze, Silver, and DuckDB paths can then be used for a second company. Keep the dashboard and DuckDB UI closed while the pipeline writes.

To inspect an existing database without changing it:

```sh
uv run --no-sync python -m sec_edgar_lakehouse.dashboard.dashboard_inspect \
  --database data/demo/query/sec_edgar.duckdb
SEC_EDGAR_DUCKDB_PATH=data/demo/query/sec_edgar.duckdb \
  uv run --no-sync streamlit run dashboard_app.py
```

The root `duckdb_startup.py` launcher opens DuckDB's browser SQL UI for **`data/query/sec_edgar.duckdb`**. Use that default path only when it is the database you mean to inspect; the demo above uses a separate database.

## Where the code lives

| Path | Responsibility |
| --- | --- |
| `src/sec_edgar_lakehouse/bronze/` | SEC discovery, paced requests, source validation, and canonical filing publication. |
| `src/sec_edgar_lakehouse/silver/` | XBRL extraction, exact values, rejected facts, versioned Parquet, and active pointers. |
| `src/sec_edgar_lakehouse/catalog/` and `src/sec_edgar_lakehouse/metadata/` | Active Silver DuckDB views and filing/fiscal metadata tied to source evidence. |
| `src/sec_edgar_lakehouse/pipeline/` | Company processing, stage gates, dbt invocation, and run summaries. |
| `transform/` | dbt concept mappings, reported/derived metric SQL, marts, and tests. |
| `src/sec_edgar_lakehouse/dashboard/` | Read-only Gold snapshot, CLI inspector, and filing explorer. |
| `tests/`, `scripts/ci/`, `.github/workflows/` | Python tests, synthetic offline validation, clean wheel checks, and CI. |

The focused design notes cover [Bronze](docs/architectures/bronze-ingestion-V1.md), [Silver](docs/architectures/silver-processing-V1.md), the [DuckDB catalog](docs/architectures/duckdb-query-catalog-V1.md), [company processing](docs/architectures/company-pipeline-orchestration-V1.md), the [end-to-end runner and metadata handoff](docs/architectures/end-to-end-pipeline-V1.md), [Gold rules](docs/architectures/gold-financial-metrics-V1.md), and the [dashboard](docs/architectures/dashboard-V1.md). [CI validation](docs/validation/ci-V1.md) records what ran locally and what still needs a hosted run.

## Boundaries

The pipeline is local, sequential, and built for one writer. Discovery uses the main SEC submissions response's `filings.recent`; it does not follow older submission files. It retains separate accessions, including amendments, without choosing a company-wide latest value. Gold supports the mapped US-GAAP concepts, not every issuer extension or XBRL taxonomy. Missing predecessor filings can leave derived quarters unavailable. Silver views point to absolute local Parquet paths, so copying only the DuckDB file will not reproduce the dataset. Scheduling, concurrent writers, and hosted dashboard deployment are not implemented.
