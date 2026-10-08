# V1 multi-company acceptance

**Result: PASS for the bounded execution, company isolation, and rerun-preservation checks.** All three CLI invocations returned exit code 0 and finalized `COMPLETE` summaries. Financial quality contains six legitimate derivation warnings: two for Apple and four for Microsoft. No execution stage failed or returned `PARTIAL`; there is no acceptance blocker.

Executed on `feat/multi-company-acceptance` on October 7, 2026 (America/Chicago); persisted timestamps use UTC on October 8. The checkout was clean before acceptance. Repository and ancestor `AGENTS.md` discovery found no applicable instruction files, and the current `docs/architectures/end-to-end-pipeline-V1.md` was read before execution.

## Purpose and scope

Validate the existing V1 CLI with two companies in one fresh local dataset, then validate one Microsoft rerun. Each selection used exact forms `10-K` and `10-Q`, inclusive filing dates `2025-01-01` through `2025-12-31`, and limit **2 total filings per company**. Selection is by filing date, so fiscal years can differ from the calendar-year filter. No extra filings were requested to fill predecessor gaps.

Only `sec_edgar_lakehouse.end_to_end_pipeline_run` performed pipeline execution. Its company processing/catalog refresh, metadata loading, and dbt build were used as implemented. Default 0.5-second request pacing, retries, terminal-block behavior, and 600-second dbt timeout were preserved. No extra build, unrelated Python suite, dependency installation, or implementation/architecture edit was performed. Inspection scripts and detailed snapshots are confined to ignored `data/`.

`SEC_USER_AGENT` was initially absent. No SEC request was made until the user supplied the prerequisite; it was injected into the CLI process environment for each authorized invocation. Its value is omitted from this report and reproduction commands.

Runtime used the existing environment: Python project requirement 3.14, DuckDB 1.5.5, dbt-core 1.12.5, and dbt-duckdb 1.11.0.

## Isolated destinations and evidence

All three invocations shared this fresh destination:

`/Users/dhirajkarki/Documents/Projects/sec-edgar-financial-data-lakehouse/data/acceptance/multi-company-7d9f697397dc4fdba9096f5d0286c138`

- `bronze/sec/filings`
- `silver/sec/filings`
- `query/sec_edgar.duckdb`
- `runs/company`
- `runs/end-to-end`
- `runs/dbt`
- `inspection`

Paths below are relative to that destination unless stated otherwise. Every invocation used a unique run ID and a previously nonexistent dbt artifact directory. The isolated database was created by the CLI; the original database was neither opened by acceptance queries nor copied.

- **Apple**: `apple-b6d2febfd49f48dea7b0c2373e98cc77`
  - Final summary: `runs/end-to-end/cik=0000320193/run_id=apple-b6d2febfd49f48dea7b0c2373e98cc77.json`
  - Company evidence: `runs/company/cik=0000320193/run_id=apple-b6d2febfd49f48dea7b0c2373e98cc77/run.json` and `submissions.json`
  - dbt artifacts: `runs/dbt/run_id=apple-b6d2febfd49f48dea7b0c2373e98cc77/`, including stdout/stderr, logs, `target/manifest.json`, and `target/run_results.json`
- **Microsoft**: `microsoft-f6a92f64744241afb381ad6300803a41`
  - Final summary: `runs/end-to-end/cik=0000789019/run_id=microsoft-f6a92f64744241afb381ad6300803a41.json`
  - Company evidence: `runs/company/cik=0000789019/run_id=microsoft-f6a92f64744241afb381ad6300803a41/run.json` and `submissions.json`
  - dbt artifacts: `runs/dbt/run_id=microsoft-f6a92f64744241afb381ad6300803a41/`, including stdout/stderr, logs, `target/manifest.json`, and `target/run_results.json`
- **Microsoft rerun**: `microsoft-rerun-9f22889a06974fc593472630bdcae663`
  - Final summary: `runs/end-to-end/cik=0000789019/run_id=microsoft-rerun-9f22889a06974fc593472630bdcae663.json`
  - Company evidence: `runs/company/cik=0000789019/run_id=microsoft-rerun-9f22889a06974fc593472630bdcae663/run.json` and `submissions.json`
  - dbt artifacts: `runs/dbt/run_id=microsoft-rerun-9f22889a06974fc593472630bdcae663/`, including stdout/stderr, logs, `target/manifest.json`, and `target/run_results.json`

Detailed acceptance evidence: `inspection/apple-snapshot.json`, `microsoft-snapshot.json`, and `rerun-snapshot.json`; three `*-comparison.json` files; `*-fiscal-source-verification.json`; `selection-and-derivation-verification.json`; `final-verification.json`; and the three CLI stdout/stderr pairs. The local scripts used to inspect these records are also retained under `inspection/`.

## Actual selected filings and fiscal metadata

| Company / CIK | Accession | Form / filed | Report date | Fiscal focus | Source XML |
| --- | --- | --- | --- | --- | --- |
| Apple / `0000320193` | `0000320193-25-000073` | 10-Q / 2025-08-01 | 2025-06-28 | 2025 Q3 | `aapl-20250628_htm.xml` |
| Apple / `0000320193` | `0000320193-25-000079` | 10-K / 2025-10-31 | 2025-09-27 | 2025 FY | `aapl-20250927_htm.xml` |
| Microsoft / `0000789019` | `0000950170-25-100235` | 10-K / 2025-07-30 | 2025-06-30 | 2025 FY | `msft-20250630_htm.xml` |
| Microsoft / `0000789019` | `0001193125-25-256321` | 10-Q / 2025-10-29 | 2025-09-30 | 2026 Q1 | `msft-20250930_htm.xml` |

All four stored fiscal extractions are `COMPLETE`, extraction version `1`; document period end equals the report date above. Selection evidence contains two distinct accessions within each company, and both companies appear in `silver.active_filings` and `gold.dim_filings`. Microsoft accession prefixes differ from its issuer CIK, so ownership was checked against stored CIK and source evidence rather than inferred from accession prefixes.

For each filing, stored form, filing date, report date, and primary document matched its own saved SEC submissions entry. Saved submissions size/checksum matched company evidence. Fiscal provenance matched filing-metadata provenance and the active document/hash. All 12 stored DEI occurrences were checked against the selected Bronze XML: element ordinal, expanded concept name, raw value, context, company identifier, resolved fiscal value, and recomputed occurrence ID. Microsoft therefore retains fiscal 2025 FY and fiscal 2026 Q1 from its own evidence.

| Company / accession | Source SHA-256 |
| --- | --- |
| Apple / `0000320193-25-000073` | `a33da25d38ddc3c39fcb80b996a26fb562b8938f942f7a41a5f156339a7d70c9` |
| Apple / `0000320193-25-000079` | `e1076735f1c81bc96d5c1ff6e1a9d23515d6eacf52b405cb1f7da3e379ac533b` |
| Microsoft / `0000950170-25-100235` | `b90704839beab78c428874d38eaaf2582e048439d53a79f52af3d2cd4ee5e792` |
| Microsoft / `0001193125-25-256321` | `2ca8a57a5cf0d9b2c3d5ec66a97a5d94d929368fd844959bdebea4e91e18ffaa` |

Each source is at `bronze/sec/filings/cik=<CIK>/accession=<accession>/sec-derived/<source XML>`. The complete active catalog rows retain publication/version paths, publication checksums, activation IDs, and timestamps in the snapshots.

## Stage outcomes

| Invocation | Company / catalog / metadata / dbt / final | Selected / complete / skipped / usable | Filing metadata inserted / existing | Fiscal metadata |
| --- | --- | --- | --- | --- |
| Apple | COMPLETE / COMPLETE / COMPLETE / COMPLETE / COMPLETE | 2 / 2 / 0 / 2 | 2 / 0 | INSERTED COMPLETE, INSERTED COMPLETE |
| Microsoft | COMPLETE / COMPLETE / COMPLETE / COMPLETE / COMPLETE | 2 / 2 / 0 / 2 | 2 / 0 | INSERTED COMPLETE, INSERTED COMPLETE |
| Microsoft rerun | COMPLETE / COMPLETE / COMPLETE / COMPLETE / COMPLETE | 2 / 0 / 2 / 2 | 0 / 2 | ALREADY_EXISTS COMPLETE, ALREADY_EXISTS COMPLETE |

All processing partial/failed/not-attempted counts, catalog failure counts, and metadata missing/conflict counts were zero. No metadata or dbt gate was skipped. Each dbt build ran 519 nodes: 1 seed, 22 view models, 404 data tests, and 92 unit tests; artifact statuses were `success=23`, `pass=496`, with no errors, warnings, or skipped nodes. These dbt test outcomes are separate from the financial issue mart.

| After invocation | Active filings | Accepted facts | Fact dimensions | Rejected facts | Gold financial / filing / issue rows |
| --- | --- | --- | --- | --- | --- |
| Apple | 2 | 1654 | 1198 | 0 | 23 / 2 / 2 |
| Microsoft | 4 | 4265 | 3470 | 0 | 39 / 4 / 6 |
| Microsoft rerun | 4 | 4265 | 3470 | 0 | 39 / 4 / 6 |

## Final counts by company

| Catalog or mart | Apple | Microsoft | Total |
| --- | --- | --- | --- |
| silver.active_filings | 2 | 2 | 4 |
| silver.facts | 1654 | 2611 | 4265 |
| silver.rejected_facts | 0 | 0 | 0 |
| silver.filing_metadata | 2 | 2 | 4 |
| silver.filing_fiscal_metadata | 2 | 2 | 4 |
| gold.fct_financial_metrics | 23 | 16 | 39 |
| gold.dim_filings | 2 | 2 | 4 |
| gold.metric_quality_issues | 2 | 4 | 6 |
| silver.fact_dimensions | 1198 | 2272 | 3470 |

| Company / accession | Reported financial rows | Derived rows | Quality warnings / errors | Filing Gold quality |
| --- | --- | --- | --- | --- |
| Apple / `0000320193-25-000073` | 11 | 0 | 2 / 0 | COMPLETE: CRITICAL_METRICS_PRESENT |
| Apple / `0000320193-25-000079` | 8 | 4 | 0 / 0 | COMPLETE: CRITICAL_METRICS_PRESENT |
| Microsoft / `0000950170-25-100235` | 8 | 0 | 4 / 0 | COMPLETE: CRITICAL_METRICS_PRESENT |
| Microsoft / `0001193125-25-256321` | 8 | 0 | 0 / 0 | COMPLETE: CRITICAL_METRICS_PRESENT |

## Representative financial values and lineage

All 39 final financial values were returned as Python `Decimal` from DuckDB `DECIMAL(38,18)`. Snapshot encoding preserves their full decimal strings and column types, with no float conversion. Display values below omit trailing fractional zeros and use full currency units, not millions. Currency measures use `{http://www.xbrl.org/2003/iso4217}USD`; diluted EPS uses `({http://www.xbrl.org/2003/iso4217}USD) / ({http://www.xbrl.org/2003/instance}shares)`. All row units were checked.

| Ref / company | Metric / origin | Fiscal focus / label | Period | Value / unit | Accession |
| --- | --- | --- | --- | --- | --- |
| R1 / Apple | revenue / reported | 2025 FY / annual | 2024-09-29 through 2025-09-27 | 416161000000 USD | `0000320193-25-000079` |
| R2 / Apple | revenue / reported | 2025 Q3 / quarter | 2025-03-30 through 2025-06-28 | 94036000000 USD | `0000320193-25-000073` |
| R3 / Apple | operating_cash_flow / reported | 2025 Q3 / year_to_date | 2024-09-29 through 2025-06-28 | 81754000000 USD | `0000320193-25-000073` |
| R4 / Apple | total_assets / reported | 2025 FY / instant | 2025-09-27 | 359241000000 USD | `0000320193-25-000079` |
| R5 / Apple | diluted_eps / reported | 2025 Q3 / quarter | 2025-03-30 through 2025-06-28 | 1.57 USD/share | `0000320193-25-000073` |
| R6 / Apple | operating_cash_flow / derived | 2025 Q4 / derived_quarter | 2025-06-29 through 2025-09-27 | 29728000000 USD | `0000320193-25-000079` |
| R7 / Microsoft | revenue / reported | 2025 FY / annual | 2024-07-01 through 2025-06-30 | 281724000000 USD | `0000950170-25-100235` |
| R8 / Microsoft | revenue / reported | 2026 Q1 / quarter | 2025-07-01 through 2025-09-30 | 77673000000 USD | `0001193125-25-256321` |
| R9 / Microsoft | operating_cash_flow / reported | 2026 Q1 / quarter | 2025-07-01 through 2025-09-30 | 45057000000 USD | `0001193125-25-256321` |
| R10 / Microsoft | total_assets / reported | 2026 Q1 / instant | 2025-09-30 | 636351000000 USD | `0001193125-25-256321` |
| R11 / Microsoft | diluted_eps / reported | 2026 Q1 / quarter | 2025-07-01 through 2025-09-30 | 3.72 USD/share | `0001193125-25-256321` |

Microsoft Q1 cash flow is reported directly for the quarter (also the first three months of its fiscal year). Microsoft has no derived row in this selection. Apple has four derived Q4 rows: revenue, net income, operating cash flow, and capital expenditures; diluted EPS is not derived by subtraction.

Representative source lineage (full supporting ID arrays are preserved in each complete snapshot):

| Ref | Current source / concept | Current occurrence ID | Supporting occurrences |
| --- | --- | --- | --- |
| R1 | `aapl-20250927_htm.xml` / RevenueFromContractWithCustomerExcludingAssessedTax | `2666a02717396489d5e45e478360973de6b25f03db04aca1d32ff0675a82334f` | 4 |
| R2 | `aapl-20250628_htm.xml` / RevenueFromContractWithCustomerExcludingAssessedTax | `027c99feb72d28e012b06a9c5322c24cce1049f13ae07566b5f41fb8907da7a2` | 2 |
| R3 | `aapl-20250628_htm.xml` / NetCashProvidedByUsedInOperatingActivities | `d294ba443cdd88a410b335adddc09b5c9d543bfd3cf11766ae8dfeb626b85f2f` | 1 |
| R4 | `aapl-20250927_htm.xml` / Assets | `267c96ba8aa5d1516761518c0a820b9ded2f59d0a23536dfc2513d88d23fe91e` | 1 |
| R5 | `aapl-20250628_htm.xml` / EarningsPerShareDiluted | `dd49ddb3d136db92d6b2a03405fc42e8b1e92f649386f182fba8960aa20a1937` | 2 |
| R6 | `aapl-20250927_htm.xml` / ANNUAL_MINUS_Q3_YTD | `8d666449ebbf6e5a9e18a56f4a8690e82ca9b8ffb566f279538a6195ba859398` | 1 |
| R7 | `msft-20250630_htm.xml` / RevenueFromContractWithCustomerExcludingAssessedTax | `5975b95775d09483654c09dcb0240a930a4c8a275b308b5c35e1c5bd3aa13ebe` | 4 |
| R8 | `msft-20250930_htm.xml` / RevenueFromContractWithCustomerExcludingAssessedTax | `60cd8ad05b04badf2fdf1f413980e08662bfa4d625ce3de932a955a0bdbdf533` | 4 |
| R9 | `msft-20250930_htm.xml` / NetCashProvidedByUsedInOperatingActivities | `01825c8e93310cf06196aaa1aa668437b6a0b2f624994a1a9c57edb844218479` | 1 |
| R10 | `msft-20250930_htm.xml` / Assets | `4a36ff533651211afaa4e7452223f9b3526d8bc2b583a5b46f0d9446bdd9521c` | 1 |
| R11 | `msft-20250930_htm.xml` / EarningsPerShareDiluted | `e707862917c3b890f6b0f4f6fc4f8a077c31b2409d415a978220a9eef140130d` | 2 |

R6 derives **111482000000 − 81754000000 = 29728000000 USD** using `ANNUAL_MINUS_Q3_YTD`, derivation rule version `1`. Its current source is Apple annual accession `0000320193-25-000079`; its predecessor is Apple Q3 accession `0000320193-25-000073`, document `aapl-20250628_htm.xml`, occurrence `d294ba443cdd88a410b335adddc09b5c9d543bfd3cf11766ae8dfeb626b85f2f`, source SHA-256 `a33da25d38ddc3c39fcb80b996a26fb562b8938f942f7a41a5f156339a7d70c9`. The prior cumulative period ends 2025-06-28; the derived quarter starts the next day. All four derived rows passed exact Decimal subtraction and same-company, same-fiscal-year, and same-unit checks.

All 87 financial supporting/current/predecessor occurrence references were resolved against `silver.facts` and checked for the expected CIK, current or predecessor accession, document, source hash, and operand value. All six quality-issue source occurrences also resolved to their own company and accession. There were no ownership, value, or lineage mismatches.

## Financial quality issues and practical meaning

All six issues are `WARNING`, stage `derivation`, code `MISSING_PREVIOUS_CUMULATIVE`. No selection or classification issue and no `ERROR` exists in this dataset.

| Company / accession | Metrics affected | Missing evidence | Practical meaning |
| --- | --- | --- | --- |
| Apple / `0000320193-25-000073` | operating_cash_flow; capital_expenditures (2 issues) | Fiscal 2025 Q2 cumulative filing | Q3 standalone cash flow cannot be derived. Q3 year-to-date values remain reported, and annual minus Q3 permits four Q4 rows. |
| Microsoft / `0000950170-25-100235` | revenue; net_income; operating_cash_flow; capital_expenditures (4 issues) | Fiscal 2025 Q3 nine-month cumulative filing | No Microsoft Q4 derived values are published. Fiscal 2026 Q1 is not a valid predecessor for fiscal 2025 FY, and Apple evidence cannot supply that gap. |

Apple issues originate in `int_quarterly_cash_flow_derivation_issues`; Microsoft issues originate in `int_q4_financial_metric_derivation_issues`. Their saved details explicitly identify the company, fiscal year, metric, and current cumulative period. No mapping was changed, no warning was suppressed, and no additional quarter was fetched. All four filing summaries remain Gold `COMPLETE` because their five critical metrics are present and no metric error exists; supplemental derivation warnings remain visible.

## Preservation and Microsoft rerun

After Apple, the baseline captured every column from all three Gold marts, both stored metadata tables, and active catalog rows with `SELECT * ... ORDER BY ALL`. Dates/timestamps, NULLs, array order, full metadata JSON, and Decimal strings were retained. Comparisons check schemas, types, and complete ordered records, not just counts.

Adding Microsoft preserved all Apple Gold/metadata/active rows exactly. It also preserved all **1,148** baseline files byte-for-byte: 34 Bronze files, 12 Silver files (including version/publication files, active pointers, and run records), 2 company evidence files, 1 finalized summary, and 1,099 dbt artifacts. The same Apple comparisons passed after the rerun.

The Microsoft rerun selected the same two accessions and safely returned `SKIPPED` for both processing and Silver publication. Both Silver skip records report `Existing version is active`, with identical previous/resulting active versions. The original Bronze manifests and version paths were reused; no new Bronze file or Silver version/active pointer was created. Filing metadata returned 0 inserted / 2 already existing; both fiscal outcomes were `ALREADY_EXISTS`, `COMPLETE`. Original metadata run IDs, submissions checksums, occurrence/issue JSON, activation IDs, and timestamps remain unchanged.

All **2,289** files present before the rerun remain byte-for-byte unchanged: 61 Bronze, 24 Silver, 4 company evidence, 2 final summaries, and 2,198 dbt artifacts. The rerun added 1,104 files: two new company evidence files, one new final summary, 1,099 fresh dbt artifacts, and two new Silver skip-evidence records. These additions preserve earlier completed evidence.

Complete Gold comparisons before versus after the rerun:

| Mart | Rows compared | Ordered-record SHA-256, identical before and after |
| --- | --- | --- |
| gold.dim_filings | 4 | `b07017e41b36fbe90da7d25df0c7aeac99b84e94025f90f40b0185545e852f12` |
| gold.fct_financial_metrics | 39 | `a44409892736a200671d743aa477add596631a8a33e87cbc1bc49c9ae739c84e` |
| gold.metric_quality_issues | 6 | `d6daaefa5404d63c1320c8a268d406cc99ed7e1cd83861aa0be83aaea4a1492e` |

The entire four-row active catalog, four filing-metadata rows, and four fiscal-metadata rows also compare exactly. Comparison records contain separate before/after hashes and explicit equality results.

## Final-summary integrity

For each new summary, the referenced company `run.json` was hashed independently and matched `company.run_record_sha256`. Both dbt JSON artifacts exist in that invocation’s requested artifact directory, and their `metadata.invocation_id` matches the summary. Node status counts were recomputed from actual `run_results.json`. Submissions evidence size/checksum also matched actual bytes. All checks passed for all three runs, and earlier summaries/artifacts stayed unchanged.

| Invocation | Company-record SHA-256 | dbt invocation ID | UTC interval |
| --- | --- | --- | --- |
| Apple | `90330453bed4f16ee97e8b8db8a3baf566d37abf39e696e7c855a259edebd3aa` | `4864e430-ea1c-4c7b-bed6-03add3205b6f` | 2026-10-08T00:20:48.387557+00:00 → 2026-10-08T00:22:12.354739+00:00 |
| Microsoft | `88a17deed2f730fffc691bc303b967bf478ded2372e8978d5de377d494c6298f` | `0a4ff35e-a68b-434d-bd6a-44e04c64b970` | 2026-10-08T00:22:34.883740+00:00 → 2026-10-08T00:24:06.262260+00:00 |
| Microsoft rerun | `54214840c720126166b86cffc0fcc6fa38a177ee30a90e72cb88b3cf49486d68` | `29257423-1ce4-4a52-b180-6a602090d607` | 2026-10-08T00:24:30.782252+00:00 → 2026-10-08T00:25:46.649706+00:00 |

## Original database and repository checks

Protected original database: `data/query/sec_edgar.duckdb`. SHA-256 before and after:

`095684c1c2572d6e202b44b262324854b813f07d61e8e9a5751b4d1c0284b52b`

The checksums are identical. The protection manifest also checked all 163 pre-existing tracked regular files: none changed. Only this new report is intended for version control; every generated dataset/artifact is under ignored `data/`. No commit, push, or merge was made.

`git diff --check` passed. No repository instruction required an additional check for this documentation-only acceptance. No redundant full dbt build or unrelated Python suite was run. Final `git status --short --untracked-files=all` shows only `?? docs/validation/multi-company-acceptance-V1.md`; the report is intentionally unstaged.

## Reproduction commands

Run from the repository root with `SEC_USER_AGENT` already configured in the environment. Do not place its value in tracked files. These commands create another fresh dataset and IDs; the three invocations below are the entire bounded pipeline sequence. Keep database readers closed while each CLI writes. Capture a complete Apple snapshot before the second invocation and a complete shared snapshot before the rerun, using the read-only snapshot method above.

```zsh
cd /Users/dhirajkarki/Documents/Projects/sec-edgar-financial-data-lakehouse
: "${SEC_USER_AGENT:?Configure SEC_USER_AGENT before requests}"
shasum -a 256 data/query/sec_edgar.duckdb
acceptance_dir="data/acceptance/multi-company-$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]')"
apple_run="apple-$(uuidgen | tr -d '-')"
microsoft_run="microsoft-$(uuidgen | tr -d '-')"
microsoft_rerun="microsoft-rerun-$(uuidgen | tr -d '-')"
mkdir -p "$acceptance_dir/bronze/sec/filings" "$acceptance_dir/silver/sec/filings" \
  "$acceptance_dir/query" "$acceptance_dir/runs/company" \
  "$acceptance_dir/runs/end-to-end" "$acceptance_dir/runs/dbt"
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

uv run --locked --group dbt python -m sec_edgar_lakehouse.end_to_end_pipeline_run \
  --cik 0000320193 "${common_args[@]}" --run-id "$apple_run" \
  --dbt-artifact-directory "$acceptance_dir/runs/dbt/run_id=$apple_run"
# Inspect Apple and snapshot complete ordered records/files before continuing.

uv run --locked --group dbt python -m sec_edgar_lakehouse.end_to_end_pipeline_run \
  --cik 0000789019 "${common_args[@]}" --run-id "$microsoft_run" \
  --dbt-artifact-directory "$acceptance_dir/runs/dbt/run_id=$microsoft_run"
# Inspect Microsoft and snapshot complete ordered records/files before continuing.

uv run --locked --group dbt python -m sec_edgar_lakehouse.end_to_end_pipeline_run \
  --cik 0000789019 "${common_args[@]}" --run-id "$microsoft_rerun" \
  --dbt-artifact-directory "$acceptance_dir/runs/dbt/run_id=$microsoft_rerun"

shasum -a 256 data/query/sec_edgar.duckdb
git diff --check
git status --short --untracked-files=all
```

To recheck this retained evidence without making SEC requests or invoking dbt:

```zsh
evidence_dir="/Users/dhirajkarki/Documents/Projects/sec-edgar-financial-data-lakehouse/data/acceptance/multi-company-7d9f697397dc4fdba9096f5d0286c138"
.venv/bin/python "$evidence_dir/inspection/compare.py" apple microsoft
.venv/bin/python "$evidence_dir/inspection/compare.py" microsoft rerun
.venv/bin/python "$evidence_dir/inspection/compare.py" apple rerun
.venv/bin/python "$evidence_dir/inspection/verify_fiscal.py" rerun
.venv/bin/python "$evidence_dir/inspection/verify_selection_and_derivations.py"
.venv/bin/python "$evidence_dir/inspection/verify_final.py"
```

The comparison commands compare retained snapshots; `verify_final.py` additionally opens only the isolated database read-only for counts and quality lineage and checks protected original-file hashes. `snapshot.py` can capture new read-only database records and file checksums into a new named snapshot; existing baseline names should be preserved.

## Limitations and blockers

No execution or acceptance blocker remains. This validates four selected filings and one Microsoft rerun in one local shared dataset. It does not establish full-year/company coverage, amendment handling, every concept mapping, or concurrent writers. The missing predecessor warnings limit the affected standalone quarter metrics as described above; they are not pipeline failures. Exact SEC submissions bytes, retrieval times, and generated IDs may differ on a later reproduction. Detailed source material, the database, snapshots, and dbt artifacts remain local and ignored rather than committed.
