# Apple and Microsoft acceptance run

**Result: passed.** Three end-to-end runs on real SEC filings: Apple, then Microsoft into the same Bronze and Silver roots and DuckDB database, then the same Microsoft selection again. Every execution stage returned `COMPLETE`. Gold reported six expected derivation warnings, explained below.

This was the first multi-company test of the pipeline, recorded on October 7, 2026 (Central time; saved timestamps are UTC on October 8). The counts below describe the pipeline on that date, before the company tables and concept fallbacks were added; the [20-company cohort](cohort.md) covers the later pipeline.

## What it was designed to show

- Two companies can share one dataset without their records mixing.
- A company whose accession numbers don't start with its own CIK is still identified correctly.
- A rerun reuses verified work and changes **nothing** that already exists, down to the byte.
- Execution status and financial quality are reported separately.

## Setup

Each company selection used exact `10-K` and `10-Q` forms, filing dates from 2025-01-01 to 2025-12-31, and a limit of **two**. Fiscal focus can differ from that calendar filing window. The runs used the standard command line, with 0.5-second request pacing, bounded retries and one dbt build per run. `SEC_USER_AGENT` came from the environment and isn't saved anywhere.

All three runs shared one ignored folder, `data/acceptance/multi-company-7d9f697397dc4fdba9096f5d0286c138/`, isolated from the main `data/query/sec_edgar.duckdb`. Snapshots, file checksums, comparison scripts and logs are under its `inspection/` folder.

| Run | Run ID | Final summary under `runs/end-to-end/` |
| --- | --- | --- |
| Apple | `apple-b6d2febfd49f48dea7b0c2373e98cc77` | `cik=0000320193/run_id=apple-b6d2febfd49f48dea7b0c2373e98cc77.json` |
| Microsoft | `microsoft-f6a92f64744241afb381ad6300803a41` | `cik=0000789019/run_id=microsoft-f6a92f64744241afb381ad6300803a41.json` |
| Microsoft rerun | `microsoft-rerun-9f22889a06974fc593472630bdcae663` | `cik=0000789019/run_id=microsoft-rerun-9f22889a06974fc593472630bdcae663.json` |

For each run ID, the company evidence is in `runs/company/cik=<CIK>/run_id=<run-id>/{run.json,submissions.json}`, and the dbt evidence, including logs, `manifest.json` and `run_results.json`, is in `runs/dbt/run_id=<run-id>/`.

## Selected filings

| Company / CIK | Accession | Form / filed | Report date | Fiscal focus | Verified XML |
| --- | --- | --- | --- | --- | --- |
| Apple / `0000320193` | `0000320193-25-000073` | 10-Q / 2025-08-01 | 2025-06-28 | 2025 Q3 | `aapl-20250628_htm.xml` |
| Apple / `0000320193` | `0000320193-25-000079` | 10-K / 2025-10-31 | 2025-09-27 | 2025 FY | `aapl-20250927_htm.xml` |
| Microsoft / `0000789019` | `0000950170-25-100235` | 10-K / 2025-07-30 | 2025-06-30 | 2025 FY | `msft-20250630_htm.xml` |
| Microsoft / `0000789019` | `0001193125-25-256321` | 10-Q / 2025-10-29 | 2025-09-30 | 2026 Q1 | `msft-20250930_htm.xml` |

Microsoft's accession prefixes differ from its CIK, so the checks used the stored issuer identity, never the prefix. Every stored form, filing date, report date and primary document matched its saved SEC listing. All four fiscal extractions were `COMPLETE`, with the document period end matching the report date. Twelve stored DEI facts were checked against the saved XML: position, concept, raw value, context, issuer, resolved fiscal value and the recomputed occurrence ID.

## Results

| Run | Selected / complete / skipped | Filing metadata | Fiscal metadata | Catalog / dbt / final |
| --- | --- | --- | --- | --- |
| Apple | 2 / 2 / 0 | 2 inserted | 2 inserted, complete | COMPLETE / COMPLETE / COMPLETE |
| Microsoft | 2 / 2 / 0 | 2 inserted | 2 inserted, complete | COMPLETE / COMPLETE / COMPLETE |
| Microsoft rerun | 2 / 0 / 2 | 2 already existing | 2 `ALREADY_EXISTS`, complete | COMPLETE / COMPLETE / COMPLETE |

No filing was partial, failed or not attempted, and there were no catalog exclusions, metadata conflicts or skipped stages. Each dbt run completed 519 nodes (one seed, 22 views, 404 data tests, 92 unit tests) with no failures or skips.

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

Apple's Q3 filing had 11 reported metrics and two warnings; its FY filing had eight reported and four derived metrics and no issues. Microsoft had eight reported metrics per filing, no derived metrics, and four warnings on its FY filing. All four filings were Gold `COMPLETE`: the five required metrics were present.

## Spot-checked values

Full USD amounts, or USD per share for EPS. Every value came back from DuckDB as a Python `Decimal` over `DECIMAL(38,18)`; no float was involved.

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

Apple's derived Q4 operating cash flow is **111,482,000,000 − 81,754,000,000 = 29,728,000,000 USD**. The annual input is occurrence `8d666449ebbf6e5a9e18a56f4a8690e82ca9b8ffb566f279538a6195ba859398` in accession `0000320193-25-000079`; the Q3 input is occurrence `d294ba443cdd88a410b335adddc09b5c9d543bfd3cf11766ae8dfeb626b85f2f` in accession `0000320193-25-000073`; the method is `ANNUAL_MINUS_Q3_YTD`. All four Apple derived values passed exact subtraction and same-company, same-fiscal-year and same-unit checks. Microsoft reported its Q1 cash flow directly, so it had no derived values in this selection.

All 87 occurrence references in Gold (current, supporting and predecessor) resolved to the expected Silver company, accession, document, checksum and value, and all six issues resolved to their own company and filing.

## The six warnings

All six are `MISSING_PREVIOUS_CUMULATIVE` warnings from the derivation step, and they are correct for this small selection:

- **Apple Q3** lacks its fiscal 2025 Q2 filing, so Gold keeps the reported Q3 year-to-date operating cash flow and capex but can't derive standalone Q3 values.
- **Microsoft FY** lacks its fiscal 2025 Q3 filing, so there is no derived Q4 revenue, net income, operating cash flow or capex. Microsoft's fiscal 2026 Q1 can't stand in for it, and neither can Apple's filings.

No mapping was changed and no extra filing was fetched to make the warnings go away.

## Rerun and preservation

After the Apple run, complete ordered records from the Gold views, the metadata tables and the active catalog were captured. Adding Microsoft left **every Apple record unchanged** and 1,148 earlier files byte-for-byte identical, including Bronze, Silver versions and pointers, company evidence, the summary and dbt artifacts.

The Microsoft rerun selected the same two filings. Both were `SKIPPED` ("Existing version is active"), and the filing and fiscal metadata returned `ALREADY_EXISTS` with their original run IDs, checksums and source evidence. All 2,289 files that existed before the rerun stayed byte-for-byte identical. The rerun only added new attempt records and new dbt artifacts; it created no new active version.

Complete ordered records (`SELECT * ... ORDER BY ALL`, including decimal text, nulls, arrays and types) matched before and after the rerun:

| Gold view | Rows | Identical ordered-record SHA-256 |
| --- | ---: | --- |
| `dim_filings` | 4 | `b07017e41b36fbe90da7d25df0c7aeac99b84e94025f90f40b0185545e852f12` |
| `fct_financial_metrics` | 39 | `a44409892736a200671d743aa477add596631a8a33e87cbc1bc49c9ae739c84e` |
| `metric_quality_issues` | 6 | `d6daaefa5404d63c1320c8a268d406cc99ed7e1cd83861aa0be83aaea4a1492e` |

The four-row active catalog and both metadata tables also matched. Each summary's company-record SHA-256 matched the actual `run.json`, and its dbt invocation ID matched the saved artifacts. The main `data/query/sec_edgar.duckdb` had the same SHA-256 before and after: `095684c1c2572d6e202b44b262324854b813f07d61e8e9a5751b4d1c0284b52b`.

## What it shows

- **Shared roots work.** The catalog refresh and dbt build rebuilt one view of **both** companies, while every filing, metadata row and fact stayed attached to its own CIK. Microsoft mattered here: because its accession prefixes don't match its CIK, a prefix-based join would have looked fine on Apple and failed on Microsoft.
- **Reruns are safe.** Bronze, Silver and metadata were reused, but the rerun still produced its own records. Comparing complete ordered rows and earlier file hashes, not just counts, proves nothing changed; a count would miss a changed decimal, a reordered ID list or a pointer moving to another filing.
- **Run status and data quality are different questions.** dbt built cleanly and every filing had its required metrics, but this small selection lacked some predecessors for standalone quarters. Gold reported the gap instead of subtracting from the wrong year or company.

This covers four filings and one rerun. It doesn't establish full company history, every taxonomy concept, amendment handling or concurrent writers.

## Reproduce it

Set your own `SEC_USER_AGENT` and keep database readers closed while each run writes. Use a fresh folder and a new artifact folder for every run, and snapshot the database between runs to repeat the preservation comparison.

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
  uv run --locked --group dbt sec-edgar pipeline \
    --cik "$cik" "${common_args[@]}" --run-id "$run_id" \
    --dbt-artifact-directory "$acceptance_dir/runs/dbt/run_id=$run_id"
}
run_company apple 0000320193
run_company microsoft 0000789019
run_company microsoft-rerun 0000789019
```

The saved `inspection/` scripts (`compare.py`, `verify_fiscal.py`, `verify_selection_and_derivations.py`, `verify_final.py`) recheck the snapshots and the isolated database without SEC requests or another build. They are local evidence, not tracked files.
