# Engineering decisions

This file explains the main choices behind the pipeline: what I picked, what I considered instead, and what each choice costs. The second half covers the bugs that changed the design. For how the system works, see the [architecture notes](../architectures/end-to-end-pipeline-V1.md). For run results, see the [cohort report](../validation/cohort-V1.md).

- [Decisions](#decisions)
- [Bugs that changed the design](#bugs-that-changed-the-design)
- [What I would change next](#what-i-would-change-next)

## Decisions

### 1. Start from raw SEC filings, not a cleaned dataset

**Context:** I wanted a project where the hard part is the data engineering, not the download.

**Decision:** Find filings through the SEC submissions and filing-index pages, and keep the actual submitted files (the XBRL instance and the complete submission text) as the source of truth.

**Alternatives:** A Kaggle dataset, or the SEC Company Facts API. Company Facts is already normalized by the SEC, so it would skip the parsing of units, periods and dimensions, which is the work I wanted to show.

**Trade-off:** Much more work and many more ways to fail (odd index pages, unusual labels, malformed XML). In return, every number can be traced back to the exact bytes it came from.

### 2. Separate keys for company, filing, fact and run

**Context:** Reruns, ticker changes and filing agents all make "which record is this?" harder than it looks.

**Decision:**
- **Company:** CIK, not ticker. Tickers change, and Alphabet has two.
- **Filing:** CIK + accession number. The accession prefix can belong to a filing agent. Microsoft's CIK is `0000789019`, but its accessions start with `0000950170` or `0001193125`.
- **Parsed document:** document name + SHA-256.
- **Fact:** a source occurrence ID based on the document and the fact's position in it.
- **Run ID:** one execution attempt. It is never part of a data key.

**Alternatives:** Ticker as the key, or folders partitioned by run date.

**Trade-off:** More columns to carry around. In return, a rerun creates new run evidence without duplicating data.

### 3. Bronze is write-once and checked before it counts

**Context:** If raw files can change silently, nothing downstream is reproducible.

**Decision:** Files download into staging first. They're checked (SEC host, non-empty body, `Content-Length` match, not an SEC block page, recognizable XML/HTML/submission structure), hashed with SHA-256, reopened and checked again after writing, and only then published to `bronze/cik=<CIK>/accession=<id>/` with a manifest. A verified file is reused when its checksum matches. An existing published filing is never overwritten; publishing over it fails so I can investigate.

**Alternatives:** Overwrite on every run (simpler, but the evidence is lost), or one folder per run date (the same filing stored many times).

**Trade-off:** If the SEC ever changes a file, the pipeline stops instead of updating. For audit-style data I'd rather stop than silently replace.

### 4. Silver keeps every fact, with exact values and explicit rejections

**Context:** One XBRL concept appears many times in a filing: current quarter, year to date, prior-year comparatives, segment breakdowns.

**Decision:** Silver keeps every recognized numeric occurrence along with its context, period, unit and dimensions. Values are exact decimals (`DECIMAL(38,18)`), never floats. A bad fact becomes a rejection row with a reason code (for example `MISSING_UNIT_REF` or `INVALID_PERIOD`) instead of being dropped. Broken shared XML or a broken context fails the whole extraction, because other facts could be misread.

**Alternatives:** Deduplicate by concept during extraction. That would throw away the period and segment context Gold needs.

**Trade-off:** Bigger tables: 601,103 facts and 643,624 dimension rows for the cohort. It also only covers a supported subset of XBRL, not every issuer's custom extensions.

### 5. Silver is versioned, and an active pointer decides what readers see

**Context:** I knew I'd fix parser bugs and need to reprocess without breaking anything that reads Silver.

**Decision:** Each parse writes a new version folder of Parquet files. Only after row counts, schema and checksums pass does `active.json` switch to it. Old versions stay on disk. The parser version (now 3) is tracked separately from the storage schema version (still 1). An identical rerun is skipped.

**Alternatives:** Overwriting in place, where a crash halfway leaves broken data. Or Delta Lake / Iceberg, which solve this properly with snapshots.

**Trade-off:** I built a small version of what Iceberg does. It's only safe with one local writer. A cloud version should use Iceberg instead.

### 6. DuckDB is a catalog of views, not a second copy of the data

**Decision:** DuckDB views point at the exact Parquet files named by verified active pointers. A refresh runs in one transaction, so if it fails, the previous catalog keeps working. Old versions are never picked up just because their folder exists.

**Alternatives:** Loading facts into DuckDB tables (a second copy that can drift from Silver), or globbing every Parquet file in the folder (which would pick up inactive versions).

**Trade-off:** The views store absolute file paths, so copying only the `.duckdb` file doesn't move the dataset.

### 7. Gold logic lives in dbt, and conflicting values are withheld

**Context:** Picking "the revenue number" for a filing means filtering on period, unit, dimensions, entity and accuracy, then choosing between the facts that remain.

**Decision:** dbt models (staging → intermediate → marts) do the selection. A [seed file](../../transform/seeds/metric_concept_map.csv) maps 8 metrics to US-GAAP concepts with priorities. Only company-wide facts (no dimensions) with the expected unit are eligible. Priority picks which concept supplies the value (decision 16). The facts of that concept must agree; if they disagree, Gold publishes no value and writes a row to `gold.metric_quality_issues`.

**Alternatives:** Doing the selection in Python, or letting priority (or row order) pick a winner even when values conflict.

**Trade-off:** Fewer filled metrics. In the cohort, 162 of 400 filings are `PARTIAL`. But every published number has a reason and a source.

### 8. Reported and derived values stay separate

**Decision:** Every row is labeled as reported or derived. Q4 is derived as annual minus Q3 year to date. Q2 and Q3 cash flow and capex are derived from cumulative values. Derived rows keep both source filings and occurrence IDs. EPS is never derived by subtraction, because share counts change between quarters. A missing earlier filing means no value plus an issue row, never zero.

**Example:** Apple FY2025 operating cash flow: $111,482M (annual) − $81,754M (Q3 year to date) = $29,728M derived Q4.

**Trade-off:** Some quarters stay empty when an earlier 10-Q is missing.

### 9. "Did the pipeline run?" and "Is the data good?" are separate statuses

**Decision:** Execution status (`COMPLETE` / `PARTIAL` / `FAILED` for a run; `SKIPPED` and `NOT_ATTEMPTED` for individual filings) is tracked separately from each filing's Gold quality status.

**Why:** In the cohort, all 571 dbt nodes pass and there are still 557 metric issue rows. Both are true, and combining them would hide one of them.

**Trade-off:** Two statuses to explain. The CLI exits with code 2 for `PARTIAL`, so a non-zero exit doesn't mean no data landed.

### 10. V1 is local, sequential, with one writer

**Context:** My original plan listed AWS services: S3, Glue, Athena, Spark, Iceberg, Airflow and Terraform.

**Decision:** I narrowed V1 to local Python, Parquet, DuckDB, dbt and Streamlit, and moved the cloud work to V2.

**Why:** The cohort is about 10 GB for 400 filings, which DuckDB handles on a laptop. I wanted correctness, reruns and lineage working before adding cloud infrastructure, and I didn't want tools without a real job.

**Trade-off:** No scheduler, no concurrent writers, no hosted pipeline. There's also no transaction across Bronze, Silver, metadata and dbt; each stage keeps its own verified output.

### 11. Being a polite SEC client

**Decision:** Every request goes through one shared pacer (by default 0.5 seconds between requests) and carries a User-Agent with contact details. A request gets at most three attempts, and only for 429/500/502/503/504, honoring `Retry-After`. A 403, an SEC block page or a `Retry-After` over 60 seconds stops the run and marks the remaining filings `NOT_ATTEMPTED`.

**Alternatives:** Parallel downloads, or retrying through blocks.

**Trade-off:** Slower backfills. Getting blocked by the SEC would be worse.

### 12. Scaling to 20 companies: plan first, then run, build Gold once

**Context:** The company runner only read `filings.recent`, processed one company per call, and ran dbt every time.

**Decision:** I added a thin cohort runner on top of the existing code instead of rewriting it:
- `plan` freezes the cohort and the exact accessions before any download. The cohort is the top 20 S&P 500 issuers by full market cap on 15 October 2026 (Alphabet's two share classes count as one CIK), with five completed fiscal years each, original 10-K and 10-Q only. It follows the SEC's older history files when `filings.recent` doesn't go back far enough.
- `execute` processes companies one after another and saves a checkpoint after each.
- `resume` checks the saved evidence and skips completed companies.
- Gold builds once after the batch, not 20 times.

**Alternatives:** Running the single-company CLI 20 times, or ranking by S&P index weight (which gives a different order).

**Trade-off:** Amendments are left out for now. All 400 filings landed, but the cohort is marked `PARTIAL`: for three JPMorgan accessions, the SEC history files they came from didn't match between planning and processing. Their filing data matched; only that source record didn't.

### 13. Company attributes come from the saved SEC response, not a lookup table

**Context:** A company table needs a name, ticker and sector. The quick way is a hand-typed CSV or a call to a market-data site.

**Decision:** `dim_companies` uses only the SEC submissions response that Bronze already saves for each company run, checked against its SHA-256 before loading. Sector is the SEC's own industry office (`ownerOrg`), industry is the SIC description, and an empty SEC field stays NULL. Each distinct response is kept as a snapshot, and the newest one wins.

**Alternatives:** A hand-maintained seed file, or GICS sectors from a market-data provider.

**Trade-off:** The SEC's grouping isn't what investors expect. NVIDIA is "Manufacturing" and Visa is "Trade & Services." Exxon has no ticker because its saved response listed none. In return, every company attribute is traceable to a saved, checksummed SEC file, like every financial value.

### 14. The company financials table only rearranges Gold

**Decision:** `fct_company_financials` pivots `fct_financial_metrics` into one row per company and fiscal period. It adds free cash flow, margins and growth, but never selects or derives a metric. A reconciliation test checks every value against its source row.

**Why:** All financial rules stay in one place. A gap in the wide table is the same gap recorded in `metric_quality_issues`, not a new one.

**Trade-off:** Some rows have holes, for example no Q4 EPS and no free cash flow when capex isn't mapped for a company.

### 15. Gold is stored as tables; staging stays as views

**Context:** Every dbt model started as a view. That kept the database small, but every test and every dashboard read recomputed the whole chain over about 600,000 Silver facts.

**Decision:** Intermediate models and marts are tables, built once per `dbt build`. Staging models stay as thin views over Silver Parquet, so decision 6 still holds: DuckDB keeps no second copy of the facts.

**Result:** The same 562-node cohort build went from 34m27s to 18.1s, and the dashboard no longer runs out of memory.

**Trade-off:** Gold is now a stored snapshot. After a catalog refresh it is stale until dbt runs again, which the pipeline already does right after the refresh.

### 16. Priority picks the concept, never the value

**Context:** The dashboard showed gaps for big companies. Some used XBRL concepts I hadn't mapped (Amazon's capex is `PaymentsToAcquireProductiveAssets`). Others reported two revenue concepts that measure different things: Walmart's total `Revenues` ($713.2B) vs. contract revenue ($706.4B). My old rule required every mapped concept to agree, so it withheld Walmart's and Berkshire's revenue for all five years.

**Decision:** Add the missing concepts as lower-priority fallbacks, put total `Revenues` first, and change the rule:
- Use the highest-priority concept the company reports. Its own facts must still agree, or the value is withheld.
- Keep every agreeing fact, of any concept, as supporting evidence.
- Record a lower-priority concept with a different value as a `LOWER_PRIORITY_CONCEPT_DIFFERS` warning instead of hiding the value.

**Alternatives:** Keep requiring all concepts to agree (safe, but it can never use a fallback: Exxon's `ProfitLoss` differs from its `NetIncomeLoss` by minority interests, so adding it would have erased Exxon's net income). Or pick by priority and say nothing about the disagreement (hides real information).

**How I checked it:** I snapshotted every Gold value before the change and compared after: 295 values gained, 0 changed, 0 lost. Annual revenue coverage went from 88 to 100 of 100 and free cash flow from 75 to 93. The full table is in the [cohort report](../validation/cohort-V1.md#concept-fallbacks-and-priority-selection).

**Trade-off:** 254 new warnings, most of them net income differing by minority interests. `ProfitLoss` as a fallback includes minority interests (0.14% of Mastercard's net income), which the docs say. Some gaps stay on purpose: bank capex and EPS for companies with several share classes.

## Bugs that changed the design

- **The complete submission file was in a `colspan` row.** The SEC filing index sometimes merges cells on that row, so normal column mapping missed or misread it. Discovery now finds that row explicitly and requires a `.txt` file name.
- **Palantir's XBRL instance "wasn't there."** My readiness check only accepted the label `EXTRACTED XBRL INSTANCE DOCUMENT`, but Palantir's FY2021 Q1 filing used `XBRL INSTANCE DOCUMENT`. Both exact labels are accepted now, and the original bytes were never changed.
- **A broken fact disappeared instead of being rejected.** A fact without `unitRef` was skipped before the rejection logic ran, so the run reported `COMPLETE` with zero rejections. Now it's counted and rejected as `MISSING_UNIT_REF` (parser revision 2). Lesson: count before you filter.
- **Prefix parsing accepted a malformed date.** A period `instant` holding a valid date followed by a nested element was accepted. Parser revision 3 checks the whole element, rejects only that fact as `INVALID_PERIOD`, and keeps the valid facts around it.
- **The XML security check depended on encoding.** A UTF-16 file with a DTD and an entity got past a check that searched raw bytes. The guard now runs inside the parser, after the encoding is known.
- **Matching garbage counted as agreement.** A revenue candidate with `decimals="not-a-valid-accuracy"` was selected, because identical invalid values "agreed." Gold now validates accuracy before comparing candidates.
- **Fiscal year was missing for some filings.** Fiscal metadata extraction missed the SEC's `dei/2021q4` namespace. Extraction version 3 recognizes it, as well as `dei/2022q4`.
- **Bad CLI input crashed while writing the run record.** Invalid forms or dates were rejected, but then the wrapper crashed trying to save them, and no `run.json` was written. Inputs are now validated before any file or network work.
- **The dashboard ran out of memory on the full cohort.** Every Gold model was a view, so a page load recomputed everything, and the reader even read `dim_filings` twice. Storing Gold as tables fixed both the memory error and the 34-minute build (decision 15).
- **dbt ran out of memory, then out of time.** Setting `threads: 1` in dbt still let DuckDB use 8 engine threads, and a lineage test ran out of memory. The offline build also hit its 900-second timeout after 515 of 538 steps. DuckDB now gets 1 thread and a memory limit, and the timeout is 1,500 seconds.
- **A new dashboard page broke the core-only install.** The Companies view added `company_page`, which imports Streamlit and Altair. The CI wheel check, which imports every module without the optional UI libraries, failed on it. Local tests had passed because Streamlit is installed in the dev environment. I added the module to the UI skip list, and extended the local "works without Streamlit" test to block Streamlit and Altair and import every other module, so the same mistake now fails on my machine first.
- **A clean install couldn't read timestamps.** Installing only the wheel showed that DuckDB needs `pytz` for timezone-aware timestamps. I added the dependency and a wheel check in CI.

## What I would change next

- **Handle multi-class EPS** (Berkshire, Visa) by choosing a documented share class instead of leaving EPS empty.
- **Make the fact table incremental**, so a new filing doesn't rebuild every Gold table. At 18 seconds a full rebuild is fine for 400 filings, but not for the whole S&P 500.
- **Replace the active pointer with Iceberg** when moving to cloud storage.
- **Make the database portable** by not storing absolute Parquet paths in the views.
- **Decide an amendment policy:** when a 10-K/A should replace the original in trend analysis.
- **Use one CLI** instead of several runner modules.
- **Resolve the three JPMorgan trace mismatches** so the cohort can be marked complete.
