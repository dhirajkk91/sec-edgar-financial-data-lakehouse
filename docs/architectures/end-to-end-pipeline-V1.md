# End-to-end run: getting from a company selection to Gold

The company runner gets filings into Bronze and Silver and refreshes the catalog. The end-to-end runner adds verified filing and fiscal metadata, runs dbt once, and saves a final summary that points back to the stage evidence. It is the local entry point for a usable Gold dataset, not another place where financial rules are implemented.

```text
Company discovery → Bronze/Silver → one catalog refresh
                                  │ usable selected filing + catalog result
                                  ▼
                         filing/fiscal metadata
                                  │ at least one usable fiscal load
                                  ▼
                              one dbt build
                                  │
                                  ▼
                         final execution summary
```

The coordinator passes the same DuckDB path to catalog refresh, metadata storage, and dbt. It checks dbt project/profile paths and requires a fresh artifact directory before it starts company work. The first catalog refresh may create the database. Each stage runs at most once per invocation; SEC request retries stay in the request layer.

The explicit path handoff avoids a quiet but serious mistake: refreshing one database and building Gold against a different default profile path. The runner sets the database for the dbt subprocess without changing the parent's environment. It does not try to coordinate concurrent writers or replay the entire run after a failure.

## Stage gates

Metadata loading starts when the company run has at least one usable selected filing and this run's catalog refresh returned `COMPLETE` or `PARTIAL`. An old database file alone is not enough. `silver.filing_metadata` stores form, filing date, report date, and primary document from the saved SEC submissions response, together with its run ID and checksum. The loader verifies that response again and checks all active catalog filings for the CIK, not just those selected by this run's limit. It counts an identical row as already existing and reports missing or conflicting fields without replacing the first provenance. A conflict for one accession blocks that accession's fiscal load while other usable filings can continue.

`silver.filing_fiscal_metadata` reads `DocumentFiscalYearFocus`, `DocumentFiscalPeriodFocus`, and `DocumentPeriodEndDate` from the verified XBRL instance for usable **selected** filings. A usable DEI item must come from the filing's issuer, have an undimensioned duration context ending on the verified report date, and agree with the form where applicable. Repeated values are fine when they agree; conflicting values leave that field unresolved. Extraction keeps every recognized occurrence and its issues even when the result is `PARTIAL`.

The fiscal row's key includes the filing, active document name and checksum, and extraction version. Before insertion or reuse, the loader rechecks the active source and filing-metadata provenance inside its transaction; an identical rerun keeps the original row, including ordered evidence. Extraction version `2` rejects malformed context content. Gold uses version `2` for an active document when present and falls back to historical version `1` otherwise. These checks keep stale or mismatched fiscal focus out of Gold's period classification.

The dbt build starts only when metadata is `COMPLETE` or `PARTIAL` and at least one fiscal load succeeded (`INSERTED` or `ALREADY_EXISTS`, with `COMPLETE` or `PARTIAL` extraction). It uses the explicit database and a new artifact directory. An empty company selection is a successful no-op that skips metadata and dbt. If an upstream gate fails, the summary records why downstream stages were skipped.

Execution uses `COMPLETE`, `PARTIAL`, or `FAILED`, with `SKIPPED` available on individual downstream stages. `PARTIAL` can mean useful earlier stages were retained even though Gold did not build; check the dbt stage before treating a run as Gold delivery. This is separate from financial quality: `gold.dim_filings` and `gold.metric_quality_issues` can report warnings after a successful dbt build.

| What happened | Final execution result | Why |
| --- | --- | --- |
| No filings matched | `COMPLETE`, metadata/dbt `SKIPPED` | A recorded no-op is not a failed pipeline. |
| No usable selected Silver | `FAILED`, downstream `SKIPPED` | There is no new selected data to build from. |
| Usable Silver but catalog refresh failed | `PARTIAL`, downstream `SKIPPED` | Published source work remains, but the current catalog handoff was not verified. |
| Metadata or dbt failed after earlier useful work | `PARTIAL` | The earlier outputs and diagnostics remain; Gold delivery needs its own stage check. |
| All stages completed, but Gold has warnings | `COMPLETE` execution | The warnings describe financial coverage, not an execution failure. |

## Evidence and failure boundaries

The company record keeps its exact submissions response and per-filing/catalog outcomes. The final end-to-end JSON includes stage statuses, skip reasons, the path and SHA-256 of that company record, and dbt artifact references. The dbt attempt keeps stdout, stderr, logs, `manifest.json`, and `run_results.json` when produced. New run IDs and artifact directories keep prior completed attempts intact.

The build runner checks its manifest and results artifacts and keeps the dbt invocation ID, so a summary can be matched to the build that actually ran. The final wrapper hashes the **saved** company record rather than reserializing an in-memory result. If final-summary writing fails, it reports that failure with the earlier stage results where available; it does not claim a completed summary or delete already published data.

```text
<company-run root>/cik=<CIK>/run_id=<run-id>/{submissions.json,run.json}
<end-to-end root>/cik=<CIK>/run_id=<run-id>.json
<dbt-artifact root>/.../{stdout.log,stderr.log,target/,logs/}
```

The CLI reads `SEC_USER_AGENT` from the environment and prints the final paths. Its module path is `sec_edgar_lakehouse.pipeline.end_to_end_pipeline_run`; see the [README](../../README.md) for a complete command.

There is no transaction spanning SEC responses, files, metadata tables, and dbt. Bronze exposes a verified canonical filing; Silver switches an active pointer after version checks; catalog refresh commits its tables/views together; and metadata loads use DuckDB transactions. A later failure does not roll these back. The final JSON is written through a temporary file and records whichever stages finished. A failed dbt build can leave some Gold relations replaced before a later model or test fails, so its artifacts and status need their own check. Connections are closed before dbt opens the database, and the local operating rule is one writer with dashboard/SQL readers closed during pipeline writes.

A company invocation selects one company's filings, but catalog refresh and dbt operate on **all** verified active filings in the shared roots/database. Loading Microsoft after Apple should leave Apple's records present. This runner does not backfill missing fiscal metadata for every older company, resolve amendments, or choose a latest filing for cross-company analysis. The [Gold design](gold-financial-metrics-V1.md) explains the rules that actually turn active facts into metrics.
