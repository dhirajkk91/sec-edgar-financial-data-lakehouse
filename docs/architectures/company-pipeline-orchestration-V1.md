# Company Pipeline Orchestration — Version 1

## Purpose

The existing pipeline can reliably process one SEC filing when its CIK and accession number are already known. That is useful as a building block, but it is not enough for a repeatable company-level run.

Version 1 of the company coordinator connects the pieces that already exist. It discovers recent filings, selects the filings we want, moves each filing through Bronze and Silver, refreshes the DuckDB catalog once, and records what happened.

The coordinator owns workflow decisions. It does not reimplement downloading, validation, XBRL parsing, Parquet writing, Silver publication, or catalog refresh logic.

## System flow

The first version processes one company at a time and runs filings sequentially.

```text
+---------------------------------------------+
| Discover recent filings from SEC            |
| Preserve exact submissions response         |
+----------------------+----------------------+
                       |
                       v
+---------------------------------------------+
| Select exact forms and date range           |
| Sort newest first and apply the run limit   |
+----------------------+----------------------+
                       |
                       v
+---------------------------------------------+
| Process each selected filing                |
| One filing outcome does not hide another    |
+----------------------+----------------------+
                       |
          +------------+------------+
          |                         |
          v                         v
+----------------------+  +----------------------+
| Verified Bronze      |  | Bronze not published |
| already exists       |  | yet                  |
+----------+-----------+  +----------+-----------+
           |                         |
           |                         v
           |              +----------------------+
           |              | Run Bronze ingestion |
           |              | and verification     |
           |              +----------+-----------+
           |                         |
           +------------+------------+
                        |
                        v
+---------------------------------------------+
| Extract and publish the Silver version      |
| Reuse an equivalent verified version        |
+----------------------+----------------------+
                       |
                       v
+---------------------------------------------+
| Refresh the DuckDB catalog once             |
| Expose all verified active Silver versions  |
+----------------------+----------------------+
                       |
                       v
+---------------------------------------------+
| Finalize the company run record             |
| COMPLETE, PARTIAL, or FAILED                |
+---------------------------------------------+
```

## Run inputs

A run needs:

- one company CIK
- a non-empty set of exact SEC form names
- optional inclusive filing-date boundaries
- an optional positive filing limit
- the SEC User-Agent
- Bronze, Silver, DuckDB, and run-record locations
- a unique run ID

The first real dataset will use Apple, exact forms `10-K` and `10-Q`, and a limit of eight filings. Amendments remain discoverable, but the first trend dataset does not select them. Gold will eventually own the rule for deciding whether an amendment supersedes an earlier filing.

Report date is metadata only. The coordinator never uses it as filing identity. A filing is identified by its normalized CIK and accession number.

## Discovery and selection

The coordinator starts with the company submissions endpoint. It preserves the exact response used for the run and calls the existing selection logic. Evidence is finalized after execution.

Selection is deterministic:

1. Match form names exactly and case-sensitively.
2. Apply the optional filing-date boundaries.
3. Sort by filing date descending.
4. Use accession number descending as the tie-breaker.
5. Apply the limit after filtering and sorting.

The selected list and outcomes are written into the final run record in processing order after the pipeline returns.

## Request handling

One company run shares one HTTP client and one request pacer across discovery and every filing ingestion. The coordinator must not reset request timing for each filing.

The existing SEC behavior remains in charge of:

- timeouts
- bounded retries
- `Retry-After`
- empty responses
- incorrect `Content-Length`
- HTTP failures
- SEC rate-limit and access-block pages

The coordinator records those outcomes. It does not create another retry system around them.

## Filing processing

Each selected filing is processed independently and produces one filing outcome.

### Reusing Bronze

The coordinator calculates the canonical Bronze path from the filing reference.

If the path does not exist, normal Bronze ingestion runs.

If the path exists, the coordinator may reuse it only when it can identify one valid published manifest for that filing. The manifest must describe the same CIK and accession number and must be usable by the existing Silver input verification.

The coordinator does not guess between multiple manifests. It does not treat a staging directory as published Bronze. It does not overwrite or delete an existing canonical filing when validation fails.

### Publishing Silver

After obtaining usable Bronze, the coordinator runs the existing Silver extraction and publication workflow.

Both Silver outcomes are successful at the orchestration level:

- `PUBLISHED` means a verified version was created or activated.
- `SKIPPED` means the equivalent verified version was already available.

The resulting Silver version must be active before the filing is considered usable downstream.

## Failure policy

An ordinary filing failure is isolated to that filing. Later selected filings continue.

Examples include:

- unusable existing Bronze
- a required document failure
- XBRL extraction failure
- Silver publication failure

A confirmed SEC access block is a run-level condition. The coordinator stops further network work instead of sending more requests. Selected filings that were not started are recorded as `NOT_ATTEMPTED` with the stop reason.

The coordinator never turns a failure into success because another filing completed.

## Per-filing outcomes

Each selected filing records one of these orchestration outcomes:

| Outcome | Meaning |
| --- | --- |
| `COMPLETE` | The filing has usable active Silver data and no partial stage result |
| `PARTIAL` | The filing produced usable active Silver data, but Bronze or Silver reported a partial result |
| `SKIPPED` | Verified Bronze and Silver work already existed, so no equivalent data was rewritten |
| `FAILED` | The filing did not produce usable active Silver data |
| `NOT_ATTEMPTED` | The run stopped before this selected filing began |

The filing record includes its reference, form, filing date, report date, stage outcomes, relevant paths, and an error stage and message when applicable.

## DuckDB refresh

DuckDB refresh runs once after filing processing, not once per filing.

Refreshing once gives readers one stable post-run catalog and avoids repeatedly rebuilding the same external views. The refresh scans all verified active Silver publications, including data created by earlier company runs.

If some filings fail but the remaining active Silver data can be cataloged, the catalog may still refresh and the company run becomes `PARTIAL`.

If catalog refresh fails after usable processing, the existing pipeline returns `PARTIAL` and catalog status `FAILED`. The catalog implementation is responsible for preserving its previous valid logical snapshot.

## Company run status

The final status summarizes the entire run:

| Status | Meaning |
| --- | --- |
| `COMPLETE` | Every selected filing completed or was safely skipped, and DuckDB refreshed successfully |
| `PARTIAL` | Usable filings remain, but processing or catalog refresh reported a partial result or failure |
| `FAILED` | Discovery failed or selected filings produced no usable Silver |

A run with no matching selected filings is a successful no-op. It records `COMPLETE`, zero selected filings, and does not pretend that data was processed.

## Run evidence

Local run evidence is stored outside Bronze and Silver data:

```text
data/runs/company/
└── cik=0000320193/
    └── run_id=<run-id>/
        ├── submissions.json
        └── run.json
```

`submissions.json` contains the exact SEC source response bytes, without parsing, reformatting, normalization, or redaction.

Each schema-version `"1"` JSON record contains the run ID, normalized CIK, UTC start and completion timestamps, selection filters, ordered filing outcomes and stage paths, processing counts, catalog status and available row counts, final status, and structured execution errors. Unavailable catalog paths and counts are null. `run.json` includes `submissions_evidence` with the relative path `submissions.json`, submissions URL, UTC retrieval time, exact byte count, and lowercase SHA-256. Source bytes are stored only in `submissions.json`; filing contents, XBRL facts, and configured SEC User-Agent or email are not added to the run record.

The reusable `execute_company_pipeline_run()` API calls the existing pipeline once. Expected `CompanyPipelineError` and `CompanyProcessingError` failures produce a `FAILED` record with a stable `PIPELINE` or `PROCESSING` error stage; unrelated programming errors propagate. Expected failures before a discovery result is available publish only `run.json` with `submissions_evidence: null`. When the pipeline returns a result, the record preserves its status and the top-level execution error is null.

This is a single-writer local implementation. Run IDs use 1–128 ASCII letters, digits, hyphens, or underscores. Managed run-root and CIK symlinks are rejected. The attempt builds evidence in a unique staging directory inside the CIK directory. Each file is written through a temporary file, flushed, fsynced, and closed. Before publication, submissions bytes are reopened and verified against their size and SHA-256, `run.json` is reopened and parsed and checked against the intended record, and staging is checked for unexpected entries. Only then is the staging directory atomically promoted with `os.replace`, with final-directory existence checks before execution and promotion. Failure cleanup removes only the staging and temporary paths owned by the attempt. Existing records are never intentionally overwritten; concurrent writers are unsupported. Persistence failure raises `CompanyRunError` without rolling back Bronze, Silver, or DuckDB outputs.

## Idempotency and reruns

Rerunning the same selection with a new run ID must not create duplicate canonical data.

- Existing verified Bronze is reused.
- Existing equivalent Silver versions are reused.
- DuckDB is rebuilt from active Silver pointers, not appended blindly.
- Every run gets a separate immutable run record.

The run ID identifies an execution. It is not part of filing identity and it does not make duplicate source data unique.

## API and command-line boundary

The orchestration logic lives in a normal Python API. The command-line interface is a thin caller that parses arguments, reads the SEC User-Agent from the environment, invokes the API, prints the summary, and returns a meaningful exit code.

Expected exit codes:

| Exit code | Result |
| --- | --- |
| `0` | `COMPLETE` |
| `2` | `PARTIAL` |
| `1` | `FAILED` or invalid invocation |

Airflow can call this same API or CLI later. Pipeline rules must not be embedded inside an Airflow DAG.

## Version 1 boundaries

This version intentionally does not add:

- parallel filing processing
- distributed workers
- multiple companies in one run
- Airflow scheduling
- dbt or Gold models
- cloud object storage
- older submissions-history file retrieval
- automatic amendment supersession
- background cleanup of retained Bronze or Silver failure staging

These are later decisions. Adding them before the sequential workflow is proven would make failures harder to understand without improving the first usable dataset.

## Acceptance criteria

Version 1 is complete when:

1. A company run discovers and deterministically selects recent filings.
2. One shared client and pacer cover all SEC requests in the run.
3. Existing verified Bronze and Silver work can be reused safely.
4. A normal filing failure does not prevent later filings from running.
5. A confirmed SEC access block stops further network requests.
6. Every selected filing has an explicit outcome.
7. DuckDB refresh runs once after filing processing.
8. Exact discovery evidence is preserved whenever discovery succeeds, along with selection configuration, ordered filing outcomes, and the finalized run record.
9. A rerun does not duplicate canonical Bronze, Silver, or catalog rows.
10. Tests cover complete, partial, failed, skipped, and stopped runs without live SEC requests.

