# Company run: selecting and processing filings

A filing ingester starts with a known accession. The company runner starts with a CIK and a bounded selection: exact forms, optional inclusive filing dates, and an optional limit. It handles **one company per invocation**, processes selected filings in order, refreshes the shared Silver catalog once, and saves the evidence for that attempt.

The SEC submissions response's `filings.recent` is the discovery source. Selection is case-sensitive on form, sorted by filing date newest first and accession descending for ties, then limited. It does not fetch the older submissions-history files. The raw submissions response is saved so the selection can be checked later. Filing identity remains CIK plus accession; report date is metadata, not a key.

Filtering happens before the limit. That makes “two 10-K/10-Q filings filed in 2025” a reproducible request rather than “take two recent filings and hope they match.” Exact forms also keep `10-K/A` and `10-Q/A` out unless they were explicitly requested. The selection keeps accessions, not an invented latest value for a fiscal period. A filing-date filter does not imply a particular fiscal year, as the Microsoft 2025 filing-date selection shows.

## Processing order

1. Discover and select the filings using one HTTP client and request pacer for the entire company run.
2. For each accession, use existing canonical Bronze only after verifying its published manifest. Otherwise ingest and verify it. Failed staging is never reusable Bronze.
3. Extract and publish Silver, or record `SKIPPED` when an equivalent version is already verified and active. A published but inactive version is not counted as usable merely because its files exist.
4. Refresh DuckDB once from **all** verified active Silver pointers under the supplied root, including earlier company runs.
5. Save a new company run directory with `submissions.json` when available and `run.json` with selection, per-filing outcomes, catalog counts, paths, and errors.

One client and pacer keep SEC timing consistent across filing boundaries. One catalog refresh gives downstream stages a single post-processing view and avoids rebuilding the same views after each accession. It also makes the scope clear: this invocation chose one company's filings, while the resulting catalog includes all active companies in the shared Silver root.

An ordinary filing failure does not hide later selected filings. A terminal SEC block stops further network work; unstarted selected filings receive `NOT_ATTEMPTED`. No outer retry loop is added around the request layer.

This is a deliberate split between a **filing problem** and a **run problem**. An invalid XBRL document for one accession need not stop the next accession. A confirmed access block is different: sending requests for the next filing would ignore the terminal signal. Both paths leave a result for every selected accession, so a partial run can be explained without inferring what happened from missing files.

| Per-filing outcome | Meaning |
| --- | --- |
| `COMPLETE` | Usable active Silver with no partial Bronze/Silver result. |
| `PARTIAL` | Usable active Silver, with a partial source or extraction result. |
| `SKIPPED` | Verified Bronze and equivalent active Silver were reused. |
| `FAILED` | This filing did not provide usable active Silver. |
| `NOT_ATTEMPTED` | The run stopped before this filing began. |

The company result is `COMPLETE` when processing is complete and the catalog refresh is complete. It is `PARTIAL` when useful Silver exists but processing or catalog refresh is partial, or catalog refresh fails after usable processing. Discovery failure or no usable selected Silver makes it `FAILED`. An empty selection is a successful no-op, with no catalog refresh needed for that selection. A catalog failure blocks metadata and dbt in the end-to-end runner even when the company result retains `PARTIAL` progress.

## Run evidence and reruns

```text
<run root>/cik=<CIK>/run_id=<run-id>/
├── submissions.json   # present when discovery returned source evidence
└── run.json
```

A run ID names an execution, not a filing or Silver version. Each run gets a new directory; existing completed evidence is not overwritten. Repeating a selection with a new run ID can reuse verified Bronze and Silver while recording a new attempt. The catalog is rebuilt from active pointers, not appended to. An unusable existing Bronze directory is reported rather than overwritten.

The record ties the saved submissions bytes to their URL, retrieval time, size, and SHA-256, and lists selected filings in processing order. If company-record publication fails after Bronze or Silver work, that data is not rolled back; the caller gets an error rather than a misleading finished run record. Keeping execution evidence separate from canonical source/version identity makes reruns inspectable without duplicating financial facts.

This layer owns workflow decisions and evidence. Bronze owns SEC requests and source publication; Silver owns extraction and version publication; the catalog owns its transaction. Metadata loading and the dbt Gold build happen in the [end-to-end runner](end-to-end-pipeline-V1.md), after this company run. There is no multi-company coordinator, scheduler, or parallel filing worker.
