# SEC EDGAR Financial Data Lakehouse

This local pipeline turns SEC 10-K and 10-Q filings into financial values you can inspect in DuckDB or a dashboard. Bronze keeps verified source files, Silver extracts recognized numeric XBRL facts, and dbt selects reported values or derives supported quarters. The recorded V1 cohort spans 20 issuers and five completed fiscal years each. Missing or conflicting financial evidence becomes a quality issue instead of a guessed number.

It runs with Python, Parquet, DuckDB, dbt, and Streamlit. A dashboard value can be traced back to the filing and the XBRL fact or facts that produced it.

The dashboard opens on a company comparison: pick companies, a metric, and annual or quarterly periods.

![Companies view: five years of revenue for five companies, from SEC filings](docs/images/companies.png)

The **Trace a filing** view follows any number back to its source. Here, Apple's FY 2025 Q4 operating cash flow is derived from the annual 10-K value minus the Q3 year-to-date value, with both inputs shown:

![Trace a filing: a derived Q4 value with its two source inputs and derivation method](docs/images/trace-a-filing.png)

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
DuckDB: active Silver catalog + verified filing/fiscal metadata + company profiles
           │
           ▼
dbt Gold: selected metrics, filing summaries, quality issues,
          company dimension, company financials by period
           │
           ▼
Read-only Streamlit dashboard: company comparison + filing explorer
```

Bronze keeps the SEC bytes before interpreting them. Silver records recognized numeric XBRL occurrences before Gold selects a metric, and it verifies a new version before changing the active pointer. DuckDB exposes active versions; dbt makes the financial selections; the dashboard reads the result. A failed dbt build does not erase an earlier verified source file.

The identities stay separate too. **CIK + accession** names a filing; the **document name + checksum** names the bytes parsed; a **source occurrence ID** names one fact in those bytes; a **Silver version** also includes parser/schema versions; a **run ID** names an execution, even when it reuses existing data. Gold then selects a metric for one accession and exact period. Keeping those keys distinct is what makes reruns and source lineage inspectable.

## What it currently covers

Gold selects eight metrics: revenue, net income, diluted EPS, total assets, total liabilities, cash and equivalents, operating cash flow, and capital expenditures. A value carries its exact period, unit, source accession, and supporting XBRL occurrence IDs. Reported annual, quarter, year-to-date, and instant values stay separate. The supported derived quarters are cash flow/capex from compatible cumulative filings and Q4 revenue, net income, cash flow, and capex from annual minus Q3 year-to-date. EPS is never derived by subtraction.

The three core Gold views are `gold.fct_financial_metrics`, `gold.dim_filings`, and `gold.metric_quality_issues`. A successful pipeline run can still have financial warnings. For example, a missing earlier 10-Q can prevent a standalone quarter from being calculated while the reported year-to-date value remains useful.

Two analysis tables sit on top of them. `gold.dim_companies` has one row per company: name, tickers, SIC industry, and sector. Every field comes from the SEC submissions response that Bronze discovery saved for the company run, checked against its recorded SHA-256 before loading; nothing is typed in or fetched from another source. *Sector* is the SEC's own industry office (`ownerOrg`, for example `06 Technology`), not a market classification such as GICS, so NVIDIA appears under Manufacturing and Visa under Trade & Services. An empty SEC field stays empty: Exxon's saved response had no tickers, so its `primary_ticker` is NULL. `gold.fct_company_financials` has one row per company and fiscal period (FY, Q1–Q4) with revenue, net income, diluted EPS, operating cash flow, capital expenditures, free cash flow, balance-sheet totals, margins, and year-over-year revenue growth side by side. It only rearranges rows from `gold.fct_financial_metrics`, so a value Gold withheld stays NULL there too. For the cohort it has 500 rows: 20 companies, five fiscal years, five periods each.

Apple's FY 2025 filing reports 111,482,000,000 USD in operating cash flow; its Q3 filing reports 81,754,000,000 USD year to date. Bronze keeps both checked XML files, Silver assigns stable IDs to the two occurrences, and the catalog exposes their active versions. Gold subtracts the compatible Q3 value to produce 29,728,000,000 USD for Q4 and keeps both accessions and occurrence IDs on the derived row. Without that Q3 filing, the annual value would still be available, but Q4 would be a warning rather than a guessed number.

The [two-company acceptance run](docs/validation/multi-company-acceptance-V1.md) used two 2025 filings each for Apple and Microsoft in one local database. It produced 39 financial rows across four filings and six derivation warnings. A Microsoft rerun reused its published inputs and left the complete ordered Gold records unchanged.

The [fixed 20-issuer cohort report](docs/validation/cohort-V1.md) is the larger local V1 check. Its [manifest](config/cohort-sp500-top20-2026-10-15.json) freezes an October 15, 2026 market-cap ranking by distinct issuer CIK, including both Alphabet share classes as one company. The plan records original 10-K and 10-Q accessions for five completed fiscal years per issuer before ingestion. Fiscal focus is checked against the filing's own DEI fields after the source is verified; it is not inferred from the calendar year alone.

| Checkpoint in that real cohort | Observed result |
| --- | ---: |
| Frozen selection | 20 CIKs, 100 annual periods, 400 planned accessions |
| Latest company attempts | 341 newly completed, 59 safely reused; none failed or unattempted |
| Published and queryable source | 400 verified Bronze publications and 400 active Silver pointers |
| Stored metadata | 400 filing rows and 400 complete fiscal version-3 rows |
| Shared catalog | 601,103 accepted facts, 643,624 dimension rows, zero rejected facts |
| Gold | 400 filing summaries, 4,015 metrics (3,349 reported; 666 derived), 487 issue rows |
| Final dbt build | All 538 nodes passed, with no failed or skipped nodes |
| Company layer (added after V1) | 25 saved run records loaded as 20 company profiles; 500 company-period rows; all 562 dbt nodes passed |
| Gold stored as tables (added after V1) | Same 562 nodes, same data: dbt build time fell from 34m27s to 18.1s |

The cohort's execution status remains `PARTIAL`: one JPMorgan accession disagreed with its history file's declared date range, and two appeared in different numbered history files when the company was processed than when the plan was saved. All three filings landed, but their planning-to-processing source trace needs review. Gold quality is separate: 190 filing summaries are `COMPLETE` and 210 `PARTIAL` under the current metric rules, with 364 error and 123 warning issue rows.

`NO_ELIGIBLE_METRIC` means the mapped facts did not pass the period, unit, dimension, source, or accuracy gates; a value conflict means eligible occurrences disagreed, so Gold withheld a winner. `UNSUPPORTED_DURATION` means the reported dates do not fit a supported fiscal-period class. `MISSING_PREVIOUS_CUMULATIVE` means a reported cumulative value remains visible but a standalone derived quarter cannot be produced from the selected evidence. The 487 issue rows refer to specific metric choices, not 487 failed filings.

## Run it locally

Use Python 3.14 and [uv](https://docs.astral.sh/uv/getting-started/installation/). From the repository root, install the locked dependencies for the pipeline, dashboard, and checks:

```sh
uv sync --locked --group dev --group dbt --group dashboard
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

For the dated 20-issuer backfill, use the cohort runner instead of running the single-company end-to-end CLI 20 times. `plan` makes paced SEC submissions requests and saves its source listings and 400 planned accessions under ignored `data/`. `execute` runs the companies sequentially, refreshing the shared catalog for metadata and building Gold once after the batch. `resume` checks the saved plan and company evidence, skips completed companies, and continues unfinished work. Give every fresh attempt its own data root; do not point this at `data/query/sec_edgar.duckdb`.

```sh
mkdir -p data
cohort_root=$(mktemp -d data/cohort-v1.XXXXXX)
manifest=config/cohort-sp500-top20-2026-10-15.json
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run plan \
  --manifest "$manifest" --data-root "$cohort_root"
# Review cohort-plan.json and its plan_gaps before downloading filings.
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run execute \
  --manifest "$manifest" --data-root "$cohort_root" \
  --dbt-timeout-seconds 43200
# If execution stops, use the same root and manifest:
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run resume \
  --manifest "$manifest" --data-root "$cohort_root" \
  --dbt-timeout-seconds 43200
uv run --no-sync python -m sec_edgar_lakehouse.dashboard.dashboard_inspect \
  --database "$cohort_root/query/sec_edgar.duckdb"
SEC_EDGAR_DUCKDB_PATH="$cohort_root/query/sec_edgar.duckdb" \
  uv run --no-sync streamlit run dashboard_app.py
```

The plan is `cohort-plan.json`; progress, per-accession outcomes, fiscal checks, and dbt attempt paths are in `cohort-state.json`. `plan` freezes accessions but downloads no filing documents. Check its gaps before `execute`. A pilot can use `execute --ranks 2 3 --build-after-batch` and then `resume` for the rest. A complete-company rerun requires `resume --ranks <rank> --rerun-complete`; if only Gold needs another attempt, use `resume --build-after-batch`. The CLI exits 2 for `PARTIAL`, so inspect the state and dbt attempt rather than assuming a nonzero exit means no data landed. Keep readers closed during writes; the cohort runner creates a fresh dbt artifact directory for each build. The SEC contact value stays in the environment and out of both files. The recorded V1 build took 29m04.79s with one DuckDB engine thread and a configured 4GB limit, when every Gold model was a view. Gold models are now stored as tables (staging stays as views over Silver), and the same 562-node build takes about 18 seconds.

In this checkout, `uv run streamlit run dashboard_app.py` opens the completed 20-issuer cohort through the ignored `data/dashboard-current.duckdb` link. The link points to the shared cohort database; the older `data/query/sec_edgar.duckdb` has not been replaced. Set `SEC_EDGAR_DUCKDB_PATH` as above to inspect a different database. In a fresh checkout without the local link, the launcher uses `data/query/sec_edgar.duckdb`.

To inspect an existing database without changing it:

```sh
uv run --no-sync python -m sec_edgar_lakehouse.dashboard.dashboard_inspect \
  --database data/demo/query/sec_edgar.duckdb
SEC_EDGAR_DUCKDB_PATH=data/demo/query/sec_edgar.duckdb \
  uv run --no-sync streamlit run dashboard_app.py
```

The root `duckdb_startup.py` launcher opens DuckDB's browser SQL UI for **`data/query/sec_edgar.duckdb`**. Use that default path only when it is the database you mean to inspect; the demo above uses a separate database.

For a cohort, the ignored data root holds distinct evidence and publication layers:

```text
cohort-plan.json, plan-sources/             frozen accessions and SEC listing bytes
cohort-state.json                           company outcomes and dbt attempts
runs/cik=<CIK>/run_id=<id>/                 saved submissions, optional history, run.json
bronze/cik=<CIK>/accession=<id>/            source files and verified manifest
silver/cik=<CIK>/accession=<id>/            active pointer, versions, attempt records
query/sec_edgar.duckdb                      catalog, metadata, and Gold relations
dbt/artifacts-<id>/                         build logs, manifest.json, run_results.json
```

New company runs load the company profile automatically during the metadata stage. A database built before company profiles existed needs a one-time backfill from the saved run records, then a Gold rebuild:

```sh
uv run --no-sync python -m sec_edgar_lakehouse.metadata.company_profile_load \
  --database "$cohort_root/query/sec_edgar.duckdb" \
  --run-record "$cohort_root"/runs/cik=*/run_id=*/run.json
uv run --no-sync python -m sec_edgar_lakehouse.pipeline.cohort_run resume \
  --manifest "$manifest" --data-root "$cohort_root" \
  --build-after-batch --dbt-timeout-seconds 43200
```

Loading the same saved response twice reports `ALREADY_EXISTS`; a newer response for the same company is kept as another snapshot, and `dim_companies` uses the newest one.

Before using Gold, check the latest `cohort-state.json.dbt_attempts` status and its `target/run_results.json`. A DuckDB file may exist even after a failed dbt model or test. The database's Silver views contain absolute Parquet paths; moving only the `.duckdb` file is not a portable copy.

The following read-only queries show coverage, Apple's filing quality and financial rows, the two source facts behind its Q4 cash-flow value, and issue counts. Point `COHORT_DB` at the database from your run.

```sh
COHORT_DB="$cohort_root/query/sec_edgar.duckdb" uv run --no-sync python - <<'PY'
import os
import duckdb

queries = {
    "coverage by issuer": """
        SELECT cik, count(*) AS filings,
               sum(financial_metric_row_count) AS metrics,
               sum(quality_issue_count) AS issues
        FROM gold.dim_filings GROUP BY cik ORDER BY cik
    """,
    "Apple filing quality": """
        SELECT accession_number, form, filing_date, report_date,
               fiscal_year_focus, fiscal_period_focus, gold_quality_status,
               missing_critical_metric_codes
        FROM gold.dim_filings WHERE cik = '0000320193'
        ORDER BY report_date DESC
    """,
    "one annual filing and its derived Q4 rows": """
        SELECT metric_code, period_label, value_origin, period_start, period_end,
               value_decimal, unit_expression, current_source_occurrence_id,
               previous_accession_number, previous_source_occurrence_id
        FROM gold.fct_financial_metrics
        WHERE cik = '0000320193' AND accession_number = '0000320193-25-000079'
        ORDER BY metric_code, period_label
    """,
    "the two Apple Q4 cash-flow inputs": """
        SELECT cik, accession_number, source_occurrence_id, source_ordinal,
               source_document_name, source_sha256, concept_local_name,
               raw_value, value_decimal, unit_expression, period_start, period_end
        FROM silver.facts
        WHERE source_occurrence_id IN (
            '8d666449ebbf6e5a9e18a56f4a8690e82ca9b8ffb566f279538a6195ba859398',
            'd294ba443cdd88a410b335adddc09b5c9d543bfd3cf11766ae8dfeb626b85f2f'
        )
        ORDER BY accession_number
    """,
    "five years of annual results by sector": """
        SELECT c.sector, c.primary_ticker, c.company_name, f.fiscal_year,
               f.revenue, f.net_income, f.free_cash_flow,
               round(f.net_margin, 3) AS net_margin,
               round(f.revenue_yoy_growth, 3) AS revenue_growth
        FROM gold.fct_company_financials AS f
        JOIN gold.dim_companies AS c USING (cik)
        WHERE f.fiscal_period = 'FY'
        ORDER BY c.sector, c.company_name, f.fiscal_year
    """,
    "issue reasons": """
        SELECT issue_code, severity, count(*) AS occurrences
        FROM gold.metric_quality_issues
        GROUP BY issue_code, severity ORDER BY occurrences DESC, issue_code
    """,
}
with duckdb.connect(
    os.environ["COHORT_DB"],
    read_only=True,
    config={"threads": 1, "memory_limit": "4GB", "preserve_insertion_order": True},
) as connection:
    for label, sql in queries.items():
        print("\n" + label)
        for row in connection.execute(sql).fetchall():
            print(row)
PY
```

To trace a displayed metric, copy its CIK, accession, current `source_occurrence_id`, and any predecessor ID from the full dashboard record. Match those IDs in `silver.facts`, then compare issuer, accession, source document/checksum, exact Decimal, unit, and period. For Apple's FY 2025 Q4 operating cash flow, the annual 111,482,000,000 USD and Q3 year-to-date 81,754,000,000 USD produce the 29,728,000,000 USD derived row. The [full walkthrough](docs/architectures/gold-financial-metrics-V1.md#trace-one-q4-value-back-to-the-filing) gives both complete occurrence IDs and source hashes.

## Where the code lives

| Path | Responsibility |
| --- | --- |
| `src/sec_edgar_lakehouse/bronze/` | SEC discovery, paced requests, source validation, and canonical filing publication. |
| `src/sec_edgar_lakehouse/silver/` | XBRL extraction, exact values, rejected facts, versioned Parquet, and active pointers. |
| `src/sec_edgar_lakehouse/catalog/` and `src/sec_edgar_lakehouse/metadata/` | Active Silver DuckDB views, plus filing/fiscal metadata and company profiles tied to source evidence. |
| `src/sec_edgar_lakehouse/pipeline/` | Company processing, stage gates, dbt invocation, and run summaries. |
| `transform/` | dbt concept mappings, reported/derived metric SQL, marts, and tests. |
| `src/sec_edgar_lakehouse/dashboard/` | Read-only Gold readers, CLI inspector, the Companies view, and the filing explorer. |
| `tests/`, `scripts/ci/`, `.github/workflows/` | Python tests, synthetic offline validation, clean wheel checks, and CI. |

For implementation details, see [Bronze](docs/architectures/bronze-ingestion-V1.md), [Silver](docs/architectures/silver-processing-V1.md), the [DuckDB catalog](docs/architectures/duckdb-query-catalog-V1.md), [company processing](docs/architectures/company-pipeline-orchestration-V1.md), the [end-to-end runner](docs/architectures/end-to-end-pipeline-V1.md), [cohort backfill](docs/architectures/cohort-backfill-V1.md), [Gold](docs/architectures/gold-financial-metrics-V1.md), and the [dashboard](docs/architectures/dashboard-V1.md). The [engineering decisions](docs/decisions/engineering-decisions.md) cover the choices and corrections behind them. Results are in the [offline CI](docs/validation/ci-V1.md), [Apple/Microsoft](docs/validation/multi-company-acceptance-V1.md), and [full cohort](docs/validation/cohort-V1.md) reports.

## Checks and recovery

The offline checks use fixtures and temporary destinations instead of live SEC responses. Dependency installation can still need network access. The dbt validation script runs a complete synthetic build. `uv build --offline` needs its build dependencies in the local cache.

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
env -u SEC_USER_AGENT uv run --no-sync pytest
uv build --offline
ci_work=$(mktemp -d "${TMPDIR:-/tmp}/sec-edgar-ci.XXXXXX")
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_offline_dbt.py \
  --work-dir "$ci_work/offline-dbt"
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_wheel.py \
  --work-dir "$ci_work/wheel"
git diff --check
```

Use the saved stage evidence before deciding what to rerun. These procedures use the `cohort_root` and `manifest` shell variables from the quickstart:

| Situation | Evidence, reuse, and next step |
| --- | --- |
| Process stopped partway through a company | Read `cohort-state.json`, then that company's `runs/cik=<CIK>/run_id=<id>/run.json` and accession outcomes. Run the `cohort_run resume` command above with the same root and manifest. Completed company records are hash-checked before they are skipped. |
| HTTP 403, SEC block page, or `Retry-After` beyond the limit | Read the terminal reason and `NOT_ATTEMPTED` accessions. Stop requests and resolve access/contact conditions before `resume`. The coordinator does not bypass a terminal response with another retry loop. |
| One filing failed or Bronze is unusable | Inspect its Bronze manifest or staging failure and the company record. Other accessions can still complete. `resume` retries unfinished company work, but an existing unverified canonical directory is not overwritten; diagnose it rather than deleting evidence blindly. |
| Fiscal metadata is partial or conflicting | Inspect the saved SEC listing, active document hash, DEI occurrences/issues, and coverage issue. Matching metadata returns `ALREADY_EXISTS`; a conflict is not replaced. After an evidence or parser correction, add `--ranks <rank> --rerun-complete --build-after-batch` to the full cohort `resume` command above, using the same manifest/root. |
| dbt failed or timed out | Inspect the latest `dbt_attempts` entry and its `stdout.log`, `stderr.log`, `target/manifest.json`, and `target/run_results.json`. Fix the cause, close readers, then run the full cohort `resume` command above with `--build-after-batch`. The runner gives this build a fresh artifact directory; earlier Gold relations may have been partly updated. |
| DuckDB is occupied | Close Streamlit, DuckDB UI, inspector, and other connections before writing. Rerun the failed stage. V1 assumes one writer and does not make concurrent publication safe. |
| Rerun finds verified input | That is expected: Bronze is rechecked, matching active Silver is `SKIPPED`, and matching metadata is `ALREADY_EXISTS`. The new run keeps separate attempt evidence; it does not need a new source copy. |

## Boundaries

The pipeline is local, sequential, and built for one writer. Ordinary company selection uses the main SEC submissions response's `filings.recent`; the cohort planner follows older submission files when that window is too short. The company CLI can retain an amendment when its form is explicitly selected; this cohort selects original `10-K` and `10-Q` filings only. Neither path chooses a company-wide latest value. Gold supports the mapped US-GAAP concepts, not every issuer extension or XBRL taxonomy. It does not convert currencies, reconcile accounting policies, or invent a quarter when predecessor evidence is missing. A catalog transaction does not make Bronze, Silver, metadata, and dbt one atomic publication. Silver views point to absolute local Parquet paths, so copying only the DuckDB file will not reproduce the dataset.

A V2 could address scheduling, cloud storage, broader taxonomy coverage, concurrent writers, and deployed dashboard access. Those require new design and validation; they are not part of this finished local V1.
