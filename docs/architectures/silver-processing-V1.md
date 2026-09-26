# SEC EDGAR Silver Processing — Version 1

Silver reads the extracted XBRL instance from a published Bronze filing and turns its numeric facts into data we can query. Each fact keeps the details needed to understand it: what was reported, for whom, for which period, in which unit, and where it came from. Gold will later decide which facts to compare and how to calculate business metrics.

## Outcome and scope

Version 1 processes one filing at a time. It produces accepted facts, their dimensions, rejected facts, and a record of the processing attempt. The output supports SQL queries and lets a reader trace a row back to the source document.

The published Apple 10-K used for the Bronze check is our first real input. The parser also needs to handle periods and contexts found in 10-Q filings. We will check that behavior with a 10-Q fixture and, later, a controlled live filing. The input is the extracted XBRL instance XML. Silver does not parse financial facts from the filing HTML or complete submission text file.

This version runs locally with one writer. Filing discovery, scheduling, cloud deployment, full taxonomy validation, and choosing the preferred business metric for a company come later.

## Bronze handoff

The caller provides a published filing directory and its Bronze manifest. Silver checks the input before it starts extracting facts:

1. The directory must be the canonical path for the manifest's CIK and accession number. The manifest must have status `COMPLETE` or `PARTIAL`, with `source_complete: true` and `parser_ready: true`. A failed staging directory is never a Silver input.
2. In `metadata/discovery.json`, find exactly one Data Files entry described as `EXTRACTED XBRL INSTANCE DOCUMENT` with an `.xml` filename. Match it to a `VERIFIED` entry in the supplied Bronze manifest's `sec-derived` section. The discovery record and manifest must identify the same filing.
3. Reopen the selected file inside `sec-derived/`. It must be a regular file in the expected directory, with no symlink in its path. Its size and SHA-256 must match the Bronze manifest.

If any check fails, Silver stops before publishing an output. Bronze's `parser_ready` flag tells us the XML was present and well formed at publication time. Silver still checks the bytes it reads and whether the XBRL structures make sense. A `COMPLETE` Bronze filing can therefore have a `PARTIAL` or `FAILED` Silver result.

Bronze Version 1 keeps one canonical directory per accession and refuses to overwrite it. Silver can distinguish source checksums, but it cannot process a changed document under an existing accession until Bronze has a way to publish that source version.

## Fact grain and identity

One row in `facts` represents one numeric fact occurrence in the extracted instance. The same concept may appear for this quarter, the year to date, a previous year, or a business segment. Even two occurrences with the same concept, period, and value stay separate until we have an explicit rule for comparing them.

Give each candidate fact an ordinal based on its position among the XML instance root's children. Count positions before deciding whether this parser accepts the item. Use the CIK, accession number, document name, document SHA-256, and ordinal to create a deterministic `source_occurrence_id`. The processing run ID does not form part of that identity. Parser and output schema versions identify how the source occurrence was processed.

If a later filing reports a different value for an earlier period, Silver keeps both occurrences. Gold can choose which reported version an analysis uses and show where it came from.

Version 1 treats direct item elements with a `unitRef` as numeric candidates and checks their `contextRef`. An identifiable bad candidate goes into the rejection count. This rule gives us a workable first parser, but it is not full XBRL taxonomy validation. If the document contains a structure that prevents us from identifying all the candidates in scope, the filing fails instead of being marked `COMPLETE`.

## Silver datasets

| Dataset | One row means | Why it exists |
| --- | --- | --- |
| `facts` | One accepted numeric occurrence | Query the reported value with its source, concept, period, unit, and entity. |
| `fact_dimensions` | One dimension on one accepted fact | Keep segment and other breakdowns without adding fixed columns for every possible axis. |
| `rejected_facts` | One identifiable candidate we could not accept | Show the raw value and references we could read, plus a reason for exclusion. |
| `processing_runs` | One attempt, including a failure or skip | Explain what ran, against which input and parser version, and what happened. |

The first `facts` schema uses these names:

| Group | Columns |
| --- | --- |
| Source | `source_occurrence_id`, `cik`, `accession_number`, `source_document_name`, `source_sha256`, `source_ordinal`, `processing_run_id`, `parser_version`, `schema_version` |
| Value | `concept_namespace`, `concept_local_name`, `raw_value`, `value_decimal`, `is_nil`, `decimals`, `precision` |
| Unit and context | `unit_ref`, `unit_expression`, `unit_xml`, `context_ref`, `context_xml`, `entity_scheme`, `entity_identifier` |
| Period | `period_kind` (`INSTANT` or `DURATION`), `period_start`, `period_end`, `period_instant` |

A duration uses start and end dates. An instant uses one date. Silver stores the period the fact actually reports. It does not label every fact in a 10-Q as a standalone quarter.

`fact_dimensions` contains `source_occurrence_id`, `axis_namespace`, `axis_name`, `member_kind`, `member_value`, `typed_member_xml`, and `context_location`. A fact may have no dimension rows or several. The context reference and context XML remain in `facts`. Having no parsed dimension row, by itself, does not prove a fact is the consolidated company total. If we cannot interpret dimensional content safely, reject the affected fact and record why.

`rejected_facts` keeps the source identity, the attributes we could read, a stable `reason_code`, and a readable `reason`. `processing_runs` records the run ID, input identity, parser and schema versions, UTC start and end times, outcome, accepted and rejected counts, published version when there is one, and any failure reason.

Keep `raw_value` as reported. For arithmetic, use an exact decimal conversion and store `value_decimal` as `DECIMAL(38,18)` in the first Parquet schema. If a value cannot fit exactly, reject that occurrence and preserve the original text in `rejected_facts`. Do not round it or convert through a binary float. An explicitly nil fact has `is_nil: true` and a null value, not zero. Keep its valid context and unit. Retain reported accuracy attributes such as `decimals` and `precision` without claiming greater accuracy.

For example, these three reported facts all belong in Silver:

| Occurrence | Concept | Reported period | Scope | Value |
| --- | --- | --- | --- | ---: |
| A | Revenue | Three months ended June 30 | Company-wide | 130 |
| B | Revenue | Six months ended June 30 | Company-wide | 230 |
| C | Revenue | Six months ended June 30 | Segment A | 40 |

The values are illustrative. A and B cover overlapping time. C may be part of B. Adding these rows would not give a meaningful total. Gold will select compatible facts before calculating a metric.

## Validation and outcomes

Start by checking the instance root and building the context and unit lookups. Then check each candidate's context, entity, period, unit, dimensions, and value. If a missing context affects one identifiable fact, reject that fact. If the shared structure is too broken to tell which facts are affected, fail the filing. Never guess a missing period, unit, or segment.

| Result | Meaning | What Silver publishes |
| --- | --- | --- |
| `COMPLETE` | All candidates in scope were accepted, including at least one non-nil numeric fact. | Facts and processing evidence. |
| `PARTIAL` | At least one non-nil numeric fact was accepted, but identifiable candidates were rejected. | Accepted facts, dimensions, rejections, and processing evidence together. |
| `FAILED` | The input, shared structure, or publication failed, or there was no accepted non-nil numeric fact. | No new active dataset. An earlier active version stays in place. |
| `SKIPPED` | The same input, parser version, and schema version already have a published output. | No new financial rows. Record the new attempt. |

`COMPLETE` measures extraction coverage. It does not promise that Gold has the revenue or cash flow fact needed for a particular chart. Likewise, a `PARTIAL` filing may still support some analyses. Gold will check metric coverage separately and will not turn missing values into zero.

Suppose 1,000 candidates include one revenue occurrence whose `contextRef` cannot be resolved. If the other 999 can be read safely, publish them as `PARTIAL` and record the missing context ID with the rejected occurrence. If an unexpected parser error interrupts the document and we cannot establish what was processed, fail the attempt. Do not publish the prefix collected before the error.

Before publication, reconcile `candidate_count = accepted_count + rejected_count`. Every rejected fact needs a reason, and every dimension row must point to an accepted fact. The original XML remains in Bronze.

## Storage and publication

Use Parquet for `facts`, `fact_dimensions`, and `rejected_facts`. DuckDB writes and queries these files. Store each `processing_runs` attempt as a structured JSON file that DuckDB can also read. That lets us record a failed or skipped attempt without rewriting an already published Parquet set. Empty row-level datasets still get a Parquet file with the declared schema.

```text
data/silver/sec/filings/
└── cik=<10-digit-cik>/
    └── accession=<accession-number>/
        ├── active.json
        ├── runs/
        │   └── run_id=<run-id>.json
        └── versions/
            └── document=<instance-filename>/
                └── source_sha256=<instance-sha256>/
                    └── parser=<parser-version>/
                        └── schema=<output-schema-version>/
                            ├── facts.parquet
                            ├── fact_dimensions.parquet
                            ├── rejected_facts.parquet
                            └── publication.json
```

`publication.json` identifies the Bronze manifest and input document. It records the parser and schema versions, Silver status, row counts, and the size and SHA-256 of each output file. Before publishing, reopen the Parquet files and check their schemas, counts, links, sizes, and hashes.

Here this writes a version into a temporary directory on the same local filesystem. Once verified, moves the whole version into place. Then replaces `active.json` with a pointer to that version. Readers resolve the pointer first and query only its files. Reading every file under `versions/` would mix current and historical outputs. Keep earlier versions so a parser correction can be inspected. A failed attempt leaves the existing active pointer alone.

An equivalent successful rerun uses the published output for the same filing, document name, source SHA-256, parser version, and schema version. It adds no fact rows. A failed attempt remains retryable. If that matching version exists but is no longer active, report it instead of silently switching back. A parser or schema change creates a new version even when the source bytes are unchanged.

Version 1 has one writer per filing. The local directory move and pointer replacement define when an output becomes visible to local readers. They are not a distributed transaction or a guarantee against power loss. Cloud object storage will need its own publication design.

## Version 1 acceptance

Tests and controlled runs should show that:

- A published, parser-ready Bronze filing produces SQL-queryable facts with source identity, entity, period, unit, and dimension information.
- Annual and comparative facts from a 10-K remain separate. A 10-Q fixture keeps three-month and year-to-date revenue as separate facts.
- A company-wide fact and a segment fact for the same concept and period stay separate. Unreadable dimensions never become a company-wide label.
- An isolated bad fact produces `PARTIAL` with reconciled counts and a useful rejection reason.
- A broken shared structure, input bytes that differ from Bronze evidence, zero accepted non-nil facts, or a failed output check leaves the previous active version untouched.
- An equivalent rerun adds no financial rows. A parser change creates a separate version.
- SQL reads the active version for a filing. An interrupted attempt cannot expose an incomplete new version.

Use small XML fixtures for periods, dimensions, rejection cases, and reruns. Also run the parser once against the published Apple filing. Passing these checks does not establish support for every XBRL taxonomy or extension.