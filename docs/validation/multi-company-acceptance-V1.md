# Apple and Microsoft: bounded V1 acceptance run

**Result: passed.** Three end-to-end CLI invocations completed: Apple, Microsoft against the same Bronze/Silver roots and DuckDB database, then the same Microsoft selection again. All execution stages returned `COMPLETE`. Gold still reported six real derivation warnings, which are explained below; none blocked the run.

This was run on October 7, 2026 (America/Chicago) on `feat/multi-company-acceptance`; saved timestamps are UTC on October 8. Each company selection used exact `10-K` and `10-Q` forms, filing dates `2025-01-01` through `2025-12-31`, and a limit of **two**. Fiscal focus can differ from that calendar-year filing filter. The run used the existing CLI and its 0.5-second request pacing, bounded retries, terminal SEC conditions, and one dbt build per invocation. `SEC_USER_AGENT` was provided to the CLI process but is not saved here.

## Dataset and selected filings

All three invocations shared the ignored local destination:

`data/acceptance/multi-company-7d9f697397dc4fdba9096f5d0286c138/`

Its `bronze/sec/filings`, `silver/sec/filings`, and `query/sec_edgar.duckdb` were isolated from the original `data/query/sec_edgar.duckdb`. Detailed snapshots, file checksums, comparison scripts, and CLI logs are under `inspection/` in that destination. Each run had a separate company record, final summary, and dbt artifact directory:

| Run | Run ID | Final summary under `runs/end-to-end/` |
| --- | --- | --- |
| Apple | `apple-b6d2febfd49f48dea7b0c2373e98cc77` | `cik=0000320193/run_id=apple-b6d2febfd49f48dea7b0c2373e98cc77.json` |
| Microsoft | `microsoft-f6a92f64744241afb381ad6300803a41` | `cik=0000789019/run_id=microsoft-f6a92f64744241afb381ad6300803a41.json` |
| Microsoft rerun | `microsoft-rerun-9f22889a06974fc593472630bdcae663` | `cik=0000789019/run_id=microsoft-rerun-9f22889a06974fc593472630bdcae663.json` |

For each ID, company evidence is at `runs/company/cik=<CIK>/run_id=<run-id>/{run.json,submissions.json}` and dbt evidence at `runs/dbt/run_id=<run-id>/`, including stdout/stderr, logs, `target/manifest.json`, and `target/run_results.json`.

| Company / CIK | Accession | Form / filed | Report date | Fiscal focus | Verified XML |
| --- | --- | --- | --- | --- | --- |
| Apple / `0000320193` | `0000320193-25-000073` | 10-Q / 2025-08-01 | 2025-06-28 | 2025 Q3 | `aapl-20250628_htm.xml` |
| Apple / `0000320193` | `0000320193-25-000079` | 10-K / 2025-10-31 | 2025-09-27 | 2025 FY | `aapl-20250927_htm.xml` |
| Microsoft / `0000789019` | `0000950170-25-100235` | 10-K / 2025-07-30 | 2025-06-30 | 2025 FY | `msft-20250630_htm.xml` |
| Microsoft / `0000789019` | `0001193125-25-256321` | 10-Q / 2025-10-29 | 2025-09-30 | 2026 Q1 | `msft-20250930_htm.xml` |

Microsoft's accession prefixes differ from its CIK; the checks used stored issuer identity and source evidence, not the prefix. Every stored filing form/date/report date/primary document matched its own saved submissions entry. All four fiscal extractions were `COMPLETE`, version `1`, with document period end matching the report date. Twelve stored DEI occurrences were checked against the saved XML, including ordinal, concept, raw value, context, issuer, resolved fiscal value, and recomputed occurrence ID. The metadata and active source provenance matched.

## Outcomes and counts

| Run | Selected / complete / skipped | Filing metadata | Fiscal metadata | Catalog / dbt / final |
| --- | --- | --- | --- | --- |
| Apple | 2 / 2 / 0 | 2 inserted | 2 inserted, complete | COMPLETE / COMPLETE / COMPLETE |
| Microsoft | 2 / 2 / 0 | 2 inserted | 2 inserted, complete | COMPLETE / COMPLETE / COMPLETE |
| Microsoft rerun | 2 / 0 / 2 | 2 already existing | 2 `ALREADY_EXISTS`, complete | COMPLETE / COMPLETE / COMPLETE |

There were no partial, failed, or unattempted filings, catalog exclusions, metadata conflicts, or skipped downstream stages. Each dbt invocation completed 519 nodes (one seed, 22 views, 404 data tests, 92 unit tests), with no failed or skipped nodes. dbt test status is separate from the Gold issue rows.

| Relation after Microsoft | Apple | Microsoft | Total |
| --- | ---: | ---: | ---: |
| `silver.active_filings` | 2 | 2 | 4 |
| `silver.facts` | 1,654 | 2,611 | 4,265 |
| `silver.fact_dimensions` | 1,198 | 2,272 | 3,470 |
| `silver.rejected_facts` | 0 | 0 | 0 |
| Filing / fiscal metadata rows | 2 / 2 | 2 / 2 | 4 / 4 |
| `gold.fct_financial_metrics` | 23 | 16 | 39 |
| `gold.dim_filings` | 2 | 2 | 4 |
| `gold.metric_quality_issues` | 2 | 4 | 6 |

Apple's Q3 had 11 reported metrics and two warnings. Its FY filing had eight reported and four derived metrics, with no issues. Microsoft had eight reported metrics per filing, no derived metrics, and four warnings on its FY filing. All four filing summaries were Gold `COMPLETE`: the five critical reported metrics were present. Counts were observed, not assumed to match across issuers.

## A few values and their sources

These are full USD amounts (or USD/share for EPS), not amounts in millions. Every final value came back from DuckDB as Python `Decimal` over `DECIMAL(38,18)`; no float conversion was used. The saved snapshots keep full fixed-scale text, unit expressions, and ordered evidence arrays.

| Company / accession | Fiscal focus and period | Metric / origin | Value | Source XML and XBRL concept |
| --- | --- | --- | ---: | --- |
| Apple / `0000320193-25-000079` | 2025 FY, 2024-09-29–2025-09-27 | Revenue, annual reported | 416,161,000,000 USD | `aapl-20250927_htm.xml` / `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Apple / `0000320193-25-000073` | 2025 Q3, 2025-03-30–2025-06-28 | Revenue, quarter reported | 94,036,000,000 USD | `aapl-20250628_htm.xml` / `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Apple / `0000320193-25-000073` | 2025 Q3, 2024-09-29–2025-06-28 | Operating cash flow, year-to-date reported | 81,754,000,000 USD | `aapl-20250628_htm.xml` / `NetCashProvidedByUsedInOperatingActivities` |
| Apple / `0000320193-25-000079` | 2025 FY, instant 2025-09-27 | Total assets, reported | 359,241,000,000 USD | `aapl-20250927_htm.xml` / `Assets` |
| Apple / `0000320193-25-000079` | 2025 Q4, 2025-06-29–2025-09-27 | Operating cash flow, derived | 29,728,000,000 USD | Annual minus Q3 year-to-date |
| Microsoft / `0000950170-25-100235` | 2025 FY, 2024-07-01–2025-06-30 | Revenue, annual reported | 281,724,000,000 USD | `msft-20250630_htm.xml` / `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Microsoft / `0001193125-25-256321` | 2026 Q1, 2025-07-01–2025-09-30 | Revenue, quarter reported | 77,673,000,000 USD | `msft-20250930_htm.xml` / `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Microsoft / `0001193125-25-256321` | 2026 Q1, instant 2025-09-30 | Total assets, reported | 636,351,000,000 USD | `msft-20250930_htm.xml` / `Assets` |
| Microsoft / `0001193125-25-256321` | 2026 Q1, 2025-07-01–2025-09-30 | Diluted EPS, quarter reported | 3.72 USD/share | `msft-20250930_htm.xml` / `EarningsPerShareDiluted` |

For Apple's derived Q4 operating cash flow, **111,482,000,000 − 81,754,000,000 = 29,728,000,000 USD**. The annual current source is accession `0000320193-25-000079`, occurrence `8d666449ebbf6e5a9e18a56f4a8690e82ca9b8ffb566f279538a6195ba859398`; the Q3 predecessor is accession `0000320193-25-000073`, occurrence `d294ba443cdd88a410b335adddc09b5c9d543bfd3cf11766ae8dfeb626b85f2f`. The stored method is `ANNUAL_MINUS_Q3_YTD`. All four Apple derived rows passed exact Decimal subtraction and same-company, same-fiscal-year, and same-unit checks. Microsoft Q1 cash flow was reported directly for its quarter; it had no derived row in this selection.

All 87 current, supporting, and predecessor financial occurrence references resolved to the expected Silver company, accession, document, checksum, and operand value. All six issue source occurrences also resolved to their own company and accession. No ownership or value mismatches were found.

## Why there are six warnings

All six are `WARNING` / `MISSING_PREVIOUS_CUMULATIVE`, at the derivation stage. Apple Q3 lacks its fiscal 2025 Q2 cumulative filing, so Gold keeps reported Q3 year-to-date operating cash flow and capex but cannot derive standalone Q3 values. Microsoft's FY filing lacks fiscal 2025 Q3 cumulative evidence, so it has no derived Q4 revenue, net income, operating cash flow, or capex. Microsoft's fiscal 2026 Q1 cannot substitute for that predecessor, and Apple's filings cannot supply it either. There were no selection/classification issues or Gold errors. No mapping was changed and no extra quarter was fetched to quiet the warnings.

## Rerun and preservation

After Apple, complete ordered records from all three Gold views, stored filing/fiscal metadata, and active catalog rows were captured. Adding Microsoft left **every Apple record unchanged** and preserved 1,148 earlier files byte-for-byte, including Bronze, Silver versions and pointers, company evidence, the final summary, and dbt artifacts.

The Microsoft rerun selected the same two accessions. Bronze and Silver reuse was verified; both filings were `SKIPPED` with “Existing version is active.” Filing and fiscal metadata returned `ALREADY_EXISTS`, retaining their original run IDs, submissions checksums, source evidence, and activation metadata. All 2,289 files that predated the rerun remained byte-for-byte unchanged. The rerun added only new attempt evidence and new dbt artifacts, including two Silver skip records; it did not create a new active version.

Complete ordered `SELECT * ... ORDER BY ALL` records, including Decimal strings, nulls, arrays, and types, matched before and after the rerun:

| Gold view | Rows | Identical ordered-record SHA-256 |
| --- | ---: | --- |
| `dim_filings` | 4 | `b07017e41b36fbe90da7d25df0c7aeac99b84e94025f90f40b0185545e852f12` |
| `fct_financial_metrics` | 39 | `a44409892736a200671d743aa477add596631a8a33e87cbc1bc49c9ae739c84e` |
| `metric_quality_issues` | 6 | `d6daaefa5404d63c1320c8a268d406cc99ed7e1cd83861aa0be83aaea4a1492e` |

The entire four-row active catalog and both four-row metadata tables also matched. Each final summary's company-record SHA-256 matched the actual `run.json`; its dbt invocation ID and node outcomes matched the saved `manifest.json` and `run_results.json`. The original database `data/query/sec_edgar.duckdb` had the same SHA-256 before and after: `095684c1c2572d6e202b44b262324854b813f07d61e8e9a5751b4d1c0284b52b`.

## What this run tells us about the architecture

The second company did more than add rows. It exercised the intended shared-root design: catalog refresh and dbt rebuilt a view of **both** companies, while accession, filing metadata, fiscal focus, and source occurrences stayed attached to their own CIKs. Microsoft was a useful check because its accession prefixes do not match its issuer CIK. A prefix-based join could have looked plausible on Apple and failed here.

The rerun tested a different boundary. Bronze/Silver and matching metadata were reused, but the execution still got new company, final-summary, dbt, and Silver skip evidence. Comparing complete ordered Gold rows and earlier file hashes showed reuse without changing previous results or provenance. A count-only comparison would have missed a changed Decimal, a reordered supporting-ID array, or a source pointer moving to another filing.

The six warnings show why execution status and financial quality cannot be folded into one flag. dbt built successfully and all five critical reported metrics were present for each filing, but this bounded selection lacked specific cumulative predecessors for standalone quarters. Gold exposed that gap instead of subtracting from the wrong fiscal year or another company. These checks support the local V1 design for this selection; they do not establish universal XBRL coverage or concurrent-writer safety.

## Reproduce the bounded run

From the repository root, configure your own `SEC_USER_AGENT` first. Keep database readers closed while each CLI writes. Create a fresh destination and a new artifact directory for **each** invocation; snapshot Apple before Microsoft and the shared database before the rerun if you want to repeat the preservation comparisons.

```zsh
: "${SEC_USER_AGENT:?Configure SEC_USER_AGENT before requests}"
acceptance_dir="data/acceptance/multi-company-$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]')"
mkdir -p "$acceptance_dir/runs/dbt"
common_args=(
  --forms 10-K 10-Q
  --bronze-directory "$acceptance_dir/bronze/sec/filings"
  --silver-directory "$acceptance_dir/silver/sec/filings"
  --database "$acceptance_dir/query/sec_edgar.duckdb"
  --run-directory "$acceptance_dir/runs/company"
  --end-to-end-run-directory "$acceptance_dir/runs/end-to-end"
  --dbt-project-directory transform --dbt-profiles-directory transform
  --filed-on-or-after 2025-01-01 --filed-on-or-before 2025-12-31 --limit 2
)
run_company() {
  local label="$1" cik="$2" run_id
  run_id="$label-$(uuidgen | tr -d '-')"
  uv run --locked --group dbt python -m sec_edgar_lakehouse.pipeline.end_to_end_pipeline_run \
    --cik "$cik" "${common_args[@]}" --run-id "$run_id" \
    --dbt-artifact-directory "$acceptance_dir/runs/dbt/run_id=$run_id"
}
run_company apple 0000320193
# Snapshot Apple before the next command.
run_company microsoft 0000789019
# Snapshot the shared database before the rerun.
run_company microsoft-rerun 0000789019
```

The retained `inspection/compare.py`, `verify_fiscal.py`, `verify_selection_and_derivations.py`, and `verify_final.py` scripts can recheck the saved snapshots and the isolated database without SEC requests or another build. Their paths are local evidence, not tracked fixtures.

This acceptance covers four selected filings and one rerun. It does not establish complete issuer history, every taxonomy concept, amendment precedence, or concurrent writers. The missing-predecessor warnings limit those standalone quarter metrics, not the execution result.
