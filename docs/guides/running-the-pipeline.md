# Running the pipeline

This is the operating guide: how to run the pipeline, inspect what it produced, and recover when something stops. How each stage works is in the [architecture docs](../architectures/end-to-end-pipeline.md). All paths are relative to the repository root, and everything under `data/` is ignored by git.

## Prerequisites

- Python 3.14 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- The locked dependencies: `make install`, which runs `uv sync --locked --group dev --group dbt --group dashboard`.
- For anything that contacts the SEC, an identified `SEC_USER_AGENT` in your environment, with your own application name and contact address. Keep it out of tracked files. The tests, the checks and the dashboard don't need it.

## Quick reference

| Task | Command |
| --- | --- |
| List the shortcuts | `make` |
| Lint, type-check and run the tests | `make check` |
| Everything the Python 3.14 CI jobs run | `make ci` |
| Open the dashboard on a local database | `make dashboard`, or `make dashboard DATABASE=path/to/sec_edgar.duckdb` |
| Open the dashboard on the committed demo, as hosted | `make demo-dashboard` |
| Rebuild the demo database from a built cohort | `make demo` |
| Serve the dashboard from Docker | `make docker-run`, then open http://localhost:8501 |
| List the pipeline commands | `uv run sec-edgar --help` |
| Show one command's options | `uv run sec-edgar <command> --help` |

Each `sec-edgar` subcommand runs an existing module, so `python -m sec_edgar_lakehouse.<module>` works too.

## Run one company

`sec-edgar pipeline` takes one company through Bronze, Silver, the catalog, metadata and Gold. This example selects up to two Apple 10-K or 10-Q filings filed in 2025 and writes under `data/demo/`:

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

It prints each stage's outcome and the paths to its summary and dbt artifacts.

- **Reruns** need a fresh `--dbt-artifact-directory`. Each run gets a new run ID automatically; pass `--run-id` to name it yourself. Verified work is reused, not downloaded again.
- **More companies** can use the same Bronze, Silver and DuckDB paths. The catalog and Gold always cover every company in them.
- **Keep the dashboard and SQL tools closed** while it runs.

## Run the 20-company cohort

Use `sec-edgar cohort` rather than running `sec-edgar pipeline` 20 times. Give every fresh attempt its own data root, never `data/query/sec_edgar.duckdb`.

```sh
mkdir -p data
cohort_root=$(mktemp -d data/cohort.XXXXXX)
manifest=config/cohort-sp500-top20-2026-10-15.json
uv run --no-sync sec-edgar cohort plan \
  --manifest "$manifest" --data-root "$cohort_root"
uv run --no-sync sec-edgar cohort execute \
  --manifest "$manifest" --data-root "$cohort_root" \
  --dbt-timeout-seconds 43200
```

1. **`plan`** makes paced SEC listing requests and freezes the 400 planned filings in `cohort-plan.json`, with the listing files under `plan-sources/`. It downloads no filings. Read `plan_gaps` in the plan before going on.
2. **`execute`** processes the companies one at a time in rank order, then builds Gold once. Progress, per-filing outcomes, fiscal checks and every dbt attempt go into `cohort-state.json`.
3. **If it stops, run `resume`** with the same root and manifest. It rechecks the plan, skips companies whose saved record still verifies, and continues the rest:

```sh
uv run --no-sync sec-edgar cohort resume \
  --manifest "$manifest" --data-root "$cohort_root" \
  --dbt-timeout-seconds 43200
```

| Goal | Add to `execute` or `resume` |
| --- | --- |
| Try a few companies first, with a Gold build | `execute --ranks 2 3 --build-after-batch`, then `resume` for the rest |
| Reprocess completed companies, for example after a parser fix | `resume --ranks <rank> ... --rerun-complete` |
| Rebuild Gold only | `resume --build-after-batch` |
| Stop after a number of companies | `--max-companies <n>` |

The command exits with code 2 when the result is `PARTIAL`. That doesn't mean nothing landed: read `cohort-state.json` and the latest dbt attempt. A full Gold build of the cohort takes about 18 seconds.

## What a run writes

```text
<data root>/
├── cohort-plan.json, plan-sources/      frozen filings and the SEC listing bytes
├── cohort-state.json                    company outcomes and dbt attempts
├── runs/cik=<CIK>/run_id=<id>/          saved listing, optional history/, run.json
├── bronze/cik=<CIK>/accession=<id>/     source files and verified manifest
├── silver/cik=<CIK>/accession=<id>/     active.json, versions/, attempt records
├── query/sec_edgar.duckdb               catalog, metadata and Gold
└── dbt/artifacts-<id>/                  logs, manifest.json, run_results.json
```

## Inspect the results

Without changing anything:

```sh
uv run --no-sync sec-edgar inspect --database "$cohort_root/query/sec_edgar.duckdb"
make dashboard DATABASE="$cohort_root/query/sec_edgar.duckdb"
```

`duckdb_startup.py` opens DuckDB's browser SQL UI on `data/query/sec_edgar.duckdb`; use it only when that is the database you mean.

**Before using Gold,** check the latest entry in `cohort-state.json` under `dbt_attempts` and its `target/run_results.json`. A database file can exist after a failed model or test.

These read-only queries show coverage per company, Apple's filing quality and financial rows, the two source facts behind its Q4 cash flow, five years of annual results by sector, and issue counts:

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

**To trace a value by hand,** copy its CIK, accession, `current_source_occurrence_id` and any predecessor ID from the dashboard's full record. Find those IDs in `silver.facts`, then compare the company, accession, source document and checksum, exact value, unit and period. Apple's FY2025 Q4 operating cash flow, 29,728,000,000 USD, comes from the annual 111,482,000,000 and the Q3 year to date 81,754,000,000. The [Gold doc](../architectures/gold-financial-metrics.md#trace-one-q4-value-back-to-the-filing) has the full walkthrough, and explains every [quality issue code](../architectures/gold-financial-metrics.md#quality-issues).

## Load company profiles from saved runs

Company runs load their profile automatically. To load profiles into a database from saved run records instead, without contacting the SEC, then rebuild Gold:

```sh
uv run --no-sync sec-edgar profiles-load \
  --database "$cohort_root/query/sec_edgar.duckdb" \
  --run-record "$cohort_root"/runs/cik=*/run_id=*/run.json
uv run --no-sync sec-edgar cohort resume \
  --manifest "$manifest" --data-root "$cohort_root" \
  --build-after-batch --dbt-timeout-seconds 43200
```

Loading the same saved response twice reports `ALREADY_EXISTS`. A newer response for the same company becomes another snapshot, and `dim_companies` uses the newest.

## Checks

None of these contact the SEC; they use fixtures and fresh temporary folders. Installing dependencies can still need the network, and `uv build --offline` needs its build dependencies cached.

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

`make ci` runs the same checks. The [CI doc](../validation/ci.md) explains what each one is designed to catch.

## Troubleshooting

Read the saved evidence before rerunning anything. The commands below use `cohort_root` and `manifest` from the cohort section.

| Symptom | Where to look | What to do |
| --- | --- | --- |
| The run stopped partway through a company | `cohort-state.json`, then that company's `runs/cik=<CIK>/run_id=<id>/run.json` | Run `resume` with the same root and manifest. Completed companies are hash-checked before they are skipped |
| HTTP 403, an SEC block page, or a `Retry-After` over 60 seconds | The stop reason and the `NOT_ATTEMPTED` filings in `run.json` | Stop. Fix the access or contact problem before `resume`; the pipeline won't retry around a block |
| One filing failed, or its Bronze folder is unusable | Its Bronze manifest or staging failure, and the company record | Other filings still complete. `resume` retries unfinished work but never overwrites an existing unverified folder; diagnose it instead of deleting evidence |
| Fiscal metadata is partial or conflicting | The saved listing, the active document hash, the DEI facts and issues, and the coverage issue | A conflict is never replaced silently. After fixing the evidence or the parser, run `resume --ranks <rank> --rerun-complete --build-after-batch` |
| dbt failed or timed out | The latest `dbt_attempts` entry: `stdout.log`, `stderr.log`, `target/manifest.json`, `target/run_results.json` | Fix the cause, close readers, then `resume --build-after-batch`. Some Gold tables may have been replaced before the failure |
| DuckDB is locked | Open connections from the dashboard, the DuckDB UI or the inspector | Close them and rerun the failed stage. The pipeline supports one writer at a time |
| A rerun reports `SKIPPED` or `ALREADY_EXISTS` | Nothing to fix | Expected: verified Bronze, matching active Silver and identical metadata are reused, and the new attempt is recorded separately |

## Operating rules

- One writer at a time, with the dashboard and SQL tools closed during writes.
- A new data root for every fresh cohort attempt.
- `SEC_USER_AGENT` stays in the environment, never in files.
- The DuckDB file can't be copied on its own, because its Silver views point to Parquet files by absolute path. To share results, use `make demo`, which copies only the Gold tables.

Scope limits, such as the eight mapped metrics, no amendment policy and no currency conversion, are listed in the [Gold doc](../architectures/gold-financial-metrics.md#trade-offs-and-limits).
