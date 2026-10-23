# Running the pipeline

This guide has the full commands for running, inspecting and recovering the pipeline. The [README](../../README.md) has the short version. Paths below are relative to the repository root, and everything under `data/` is ignored by git.

## Setup

Use Python 3.14 and [uv](https://docs.astral.sh/uv/getting-started/installation/), then install the locked dependencies for the pipeline, dashboard and checks:

```sh
make install
```

`make` on its own lists the shortcuts. The pipeline's tools share one command, `sec-edgar`: `uv run sec-edgar --help` lists the subcommands and `uv run sec-edgar <command> --help` shows each one's options. Each subcommand runs an existing module, so `python -m sec_edgar_lakehouse.<module>` still works and appears in older reports.

| Task | Command |
| --- | --- |
| Install the locked dependencies | `make install` |
| Lint, type-check and run the tests | `make check` |
| Run everything the Python 3.14 CI jobs run | `make ci` |
| Open the dashboard on your local database | `make dashboard`, or `make dashboard DATABASE=path/to/sec_edgar.duckdb` |
| Open the dashboard on the committed demo, as hosted | `make demo-dashboard` |
| Rebuild the demo database from a built cohort | `make demo` |
| Serve the dashboard from Docker | `make docker-run`, then open http://localhost:8501 |

## Run one company, then the 20-company cohort

SEC requests need an identified `SEC_USER_AGENT` in your environment. Use your own application name and contact address; keep the value out of tracked files. `sec-edgar pipeline` processes **one company per invocation**. This example selects up to two Apple 10-K/10-Q filings filed in 2025 and writes to ignored `data/demo/` paths:

```sh
export SEC_USER_AGENT="Your App Name your.email@example.com"
mkdir -p data/demo/runs/dbt
uv run --no-sync sec-edgar pipeline \
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

For the dated 20-issuer backfill, use `sec-edgar cohort` instead of running `sec-edgar pipeline` 20 times. `plan` makes paced SEC submissions requests and saves its source listings and 400 planned accessions under ignored `data/`. `execute` runs the companies sequentially, refreshing the shared catalog for metadata and building Gold once after the batch. `resume` checks the saved plan and company evidence, skips completed companies, and continues unfinished work. Give every fresh attempt its own data root; do not point this at `data/query/sec_edgar.duckdb`.

```sh
mkdir -p data
cohort_root=$(mktemp -d data/cohort-v1.XXXXXX)
manifest=config/cohort-sp500-top20-2026-10-15.json
uv run --no-sync sec-edgar cohort plan \
  --manifest "$manifest" --data-root "$cohort_root"
# Review cohort-plan.json and its plan_gaps before downloading filings.
uv run --no-sync sec-edgar cohort execute \
  --manifest "$manifest" --data-root "$cohort_root" \
  --dbt-timeout-seconds 43200
# If execution stops, use the same root and manifest:
uv run --no-sync sec-edgar cohort resume \
  --manifest "$manifest" --data-root "$cohort_root" \
  --dbt-timeout-seconds 43200
uv run --no-sync sec-edgar inspect \
  --database "$cohort_root/query/sec_edgar.duckdb"
SEC_EDGAR_DUCKDB_PATH="$cohort_root/query/sec_edgar.duckdb" \
  uv run --no-sync streamlit run dashboard_app.py
```

The plan is `cohort-plan.json`; progress, per-accession outcomes, fiscal checks, and dbt attempt paths are in `cohort-state.json`. `plan` freezes accessions but downloads no filing documents. Check its gaps before `execute`. A pilot can use `execute --ranks 2 3 --build-after-batch` and then `resume` for the rest. A complete-company rerun requires `resume --ranks <rank> --rerun-complete`; if only Gold needs another attempt, use `resume --build-after-batch`. The CLI exits 2 for `PARTIAL`, so inspect the state and dbt attempt rather than assuming a nonzero exit means no data landed. Keep readers closed during writes; the cohort runner creates a fresh dbt artifact directory for each build. The SEC contact value stays in the environment and out of both files. The recorded V1 build took 29m04.79s with one DuckDB engine thread and a configured 4GB limit, when every Gold model was a view. Gold models are now stored as tables (staging stays as views over Silver), and the same 562-node build takes about 18 seconds.

In this checkout, `uv run streamlit run dashboard_app.py` opens the completed 20-issuer cohort through the ignored `data/dashboard-current.duckdb` link. The link points to the shared cohort database; the older `data/query/sec_edgar.duckdb` has not been replaced. Set `SEC_EDGAR_DUCKDB_PATH` as above to inspect a different database. In a fresh checkout without the local link, the launcher uses `data/query/sec_edgar.duckdb`.

To inspect an existing database without changing it:

```sh
uv run --no-sync sec-edgar inspect \
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
uv run --no-sync sec-edgar profiles-load \
  --database "$cohort_root/query/sec_edgar.duckdb" \
  --run-record "$cohort_root"/runs/cik=*/run_id=*/run.json
uv run --no-sync sec-edgar cohort resume \
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

To trace a displayed metric, copy its CIK, accession, current `source_occurrence_id`, and any predecessor ID from the full dashboard record. Match those IDs in `silver.facts`, then compare issuer, accession, source document/checksum, exact Decimal, unit, and period. For Apple's FY 2025 Q4 operating cash flow, the annual 111,482,000,000 USD and Q3 year-to-date 81,754,000,000 USD produce the 29,728,000,000 USD derived row. The [full walkthrough](../architectures/gold-financial-metrics-V1.md#trace-one-q4-value-back-to-the-filing) gives both complete occurrence IDs and source hashes.

## Reading the quality issues

`NO_ELIGIBLE_METRIC` means the mapped facts did not pass the period, unit, dimension, source, or accuracy gates; a value conflict means occurrences of the same concept disagreed, so Gold withheld a winner. `LOWER_PRIORITY_CONCEPT_DIFFERS` is a warning: Gold used the highest-priority concept, and a lower-priority one reports a different value, such as Walmart's contract revenue next to its total `Revenues`. `UNSUPPORTED_DURATION` means the reported dates do not fit a supported fiscal-period class. `MISSING_PREVIOUS_CUMULATIVE` means a reported cumulative value remains visible but a standalone derived quarter cannot be produced from the selected evidence. The 557 issue rows refer to specific metric choices, not 557 failed filings.

## Checks and recovery

The offline checks use fixtures and temporary destinations instead of live SEC responses. Dependency installation can still need network access. The dbt validation script runs a complete synthetic build. `uv build --offline` needs its build dependencies in the local cache.

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests deploy
env -u SEC_USER_AGENT uv run --no-sync pytest
uv build --offline
ci_work=$(mktemp -d "${TMPDIR:-/tmp}/sec-edgar-ci.XXXXXX")
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_offline_dbt.py \
  --work-dir "$ci_work/offline-dbt"
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_wheel.py \
  --work-dir "$ci_work/wheel"
git diff --check
```

`make ci` runs the same checks, each in a fresh temporary directory.

Use the saved stage evidence before deciding what to rerun. These procedures use the `cohort_root` and `manifest` shell variables from the cohort commands above:

| Situation | Evidence, reuse, and next step |
| --- | --- |
| Process stopped partway through a company | Read `cohort-state.json`, then that company's `runs/cik=<CIK>/run_id=<id>/run.json` and accession outcomes. Run the `sec-edgar cohort resume` command above with the same root and manifest. Completed company records are hash-checked before they are skipped. |
| HTTP 403, SEC block page, or `Retry-After` beyond the limit | Read the terminal reason and `NOT_ATTEMPTED` accessions. Stop requests and resolve access/contact conditions before `resume`. The coordinator does not bypass a terminal response with another retry loop. |
| One filing failed or Bronze is unusable | Inspect its Bronze manifest or staging failure and the company record. Other accessions can still complete. `resume` retries unfinished company work, but an existing unverified canonical directory is not overwritten; diagnose it rather than deleting evidence blindly. |
| Fiscal metadata is partial or conflicting | Inspect the saved SEC listing, active document hash, DEI occurrences/issues, and coverage issue. Matching metadata returns `ALREADY_EXISTS`; a conflict is not replaced. After an evidence or parser correction, add `--ranks <rank> --rerun-complete --build-after-batch` to the full cohort `resume` command above, using the same manifest/root. |
| dbt failed or timed out | Inspect the latest `dbt_attempts` entry and its `stdout.log`, `stderr.log`, `target/manifest.json`, and `target/run_results.json`. Fix the cause, close readers, then run the full cohort `resume` command above with `--build-after-batch`. The runner gives this build a fresh artifact directory; earlier Gold relations may have been partly updated. |
| DuckDB is occupied | Close Streamlit, DuckDB UI, inspector, and other connections before writing. Rerun the failed stage. V1 assumes one writer and does not make concurrent publication safe. |
| Rerun finds verified input | That is expected: Bronze is rechecked, matching active Silver is `SKIPPED`, and matching metadata is `ALREADY_EXISTS`. The new run keeps separate attempt evidence; it does not need a new source copy. |

## Boundaries

The pipeline is local, sequential, and built for one writer. Ordinary company selection uses the main SEC submissions response's `filings.recent`; the cohort planner follows older submission files when that window is too short. The company CLI can retain an amendment when its form is explicitly selected; this cohort selects original `10-K` and `10-Q` filings only. Neither path chooses a company-wide latest value. Gold supports the mapped US-GAAP concepts, not every issuer extension or XBRL taxonomy. It does not convert currencies, reconcile accounting policies, or invent a quarter when predecessor evidence is missing. A catalog transaction does not make Bronze, Silver, metadata, and dbt one atomic publication. Silver views point to absolute local Parquet paths, so copying only the DuckDB file will not reproduce the dataset; the hosted demo copies only Gold tables for that reason. It is a read-only snapshot that I re-export after a Gold rebuild, not a deployed pipeline.
