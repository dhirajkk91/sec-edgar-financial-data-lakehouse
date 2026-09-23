# SEC EDGAR Bronze Ingestion — Version 1

This document defines the first version of the SEC filing ingestion pipeline. Version 1 handles one filing at a time and writes a complete, verifiable copy of the source material to the Bronze layer.

The Bronze layer is an archive of what we received from the SEC. It does not contain parsed financial facts or cleaned business tables.

## Version 1 boundary

The pipeline accepts either a filing index URL or a CIK and accession number. A successful run produces:

- the filing documents required by the source contract;
- the complete submission text file;
- the SEC responses used to discover the filing;
- a manifest describing what was requested, downloaded, verified, and stored.

Version 1 runs locally and processes a single filing. Multi-filing discovery, cloud storage, orchestration, XBRL parsing, and Silver/Gold models are outside this version and will be added later on. 

## Filing identity

The accession number is the filing identifier. CIK identifies the filer and is stored as a zero-padded 10-character string.

```text
filer key:        cik
filing key:       accession_number
document key:     accession_number + document_name
document version: accession_number + document_name + sha256
```

Ticker is useful for search and display, but it is not stable enough to identify a company. EIN, form type, filing date, and report period are metadata. Report period is not part of the key because it can be corrected, repeated, or reported inconsistently.

An amended filing has its own accession number and is stored as a separate filing. It does not replace the original submission.

## Source contract

Discovery starts from the SEC filing index. The pipeline builds an inventory before downloading any filing documents. Each inventory entry records the source URL, document name, SEC document type, sequence number when available, and whether the file is required for Bronze completeness or parser readiness.

Artifacts are handled as follows:

| Artifact | Bronze handling |
| --- | --- |
| Documents listed in the SEC submission table | Preserve and require for source completeness |
| Complete submission `.txt` | Preserve and require for source completeness |
| Filing index or index JSON used for discovery | Preserve as discovery metadata |
| SEC-generated XBRL support files | Preserve when available; do not fail source completeness if absent |
| Viewer-only CSS, JavaScript, and rendered report pages | Ignore unless explicitly listed as submitted documents |
| Unrecognized item in the submission table | Preserve and flag in the manifest |

This distinction matters because the SEC filing page contains both submitted material and files generated to support the web viewer. Bronze completeness is based on the submission, not on whether the SEC viewer can be reconstructed.

## Completeness

The pipeline reports two independent conditions:

- `source_complete`: every artifact required by the source contract is verified in Bronze.
- `parser_ready`: every artifact required by the configured parser contract is verified in Bronze.

A filing can be parser-ready without being source-complete. For example, the primary Inline XBRL document may be present while a non-parser exhibit is missing. The run must still be reported as partial because the Bronze copy is incomplete.

Parser requirements belong in configuration rather than downloader code. For Version 1, the default parser contract requires the primary Inline XBRL document and the referenced extension schema and linkbases. The complete submission text file remains mandatory for source completeness even when the parser does not use it.

## Ingestion flow

1. Resolve the filing and read the SEC index.
2. Build the expected document inventory.
3. Classify each artifact and assign its completion requirements.
4. Download files and store them in a unique filing-level staging directory outside canonical Bronze, on the same filesystem.
5. Reopen staged files to verify their byte counts and SHA-256 checksums. Require every source-completeness artifact; record missing or failed optional files and remove their unverified bytes before publication.
6. Prepare the `COMPLETE` or `PARTIAL` run manifest in staging, then publish the verified filing directory to canonical Bronze in one directory move. Refuse an existing canonical filing directory.
7. Return the published filing path and manifest path.

Files are never written directly to their final path. Missing or failed required files leave canonical Bronze untouched; verified files and a failure manifest remain in staging for diagnosis. Reusing a preserved staging directory requires a later increment.

## Storage layout

CIK and accession number determine the canonical location. Run date is metadata and is not part of the filing path.

```text
data/bronze/sec/filings/
└── cik=0000320193/
    └── accession=0000320193-26-000013/
        ├── submitted/
        ├── submission-package/
        ├── sec-derived/
        ├── metadata/
        └── manifests/
            └── run_id=<run-id>.json
```

`submitted/` keeps the documents reported in the submission table. `submission-package/` contains the complete submission text file. `sec-derived/` is reserved for optional SEC-generated XBRL support files. New local runs require `metadata/filing-index.html` (the exact discovery response) and `metadata/discovery.json` (the normalized discovery record) to verify before publication.

Invalid or conflicting content is kept outside the canonical filing directory:

```text
data/quarantine/
└── cik=<cik>/accession=<accession>/document=<name>/observed_at=<timestamp>/
```

The same layout can later be moved under an S3 prefix without changing filing identity or downstream paths.

## Idempotency and content changes

Bronze files are immutable after verification.

The first local publisher refuses an existing canonical filing directory. Later idempotent reruns follow this policy:

- If the destination does not exist, verify and store the file.
- If it exists with the same checksum, leave it in place and record `SKIPPED_IDENTICAL`.
- If the same logical document is returned with different bytes, quarantine the new response and record `CONTENT_CHANGE_DETECTED`.
- If only an incomplete temporary file exists, discard that temporary file and retry the download.

There is no automatic overwrite path for verified Bronze data. A changed SEC response must be reviewed before it becomes current, and both versions must remain available if the change is accepted.

## Download validation

HTTP `200` alone is not enough to accept a response. Before promotion, the downloader verifies:

- the final URL is on an allowed SEC host and belongs to the expected filing;
- the response body is non-empty;
- the byte count matches `Content-Length` when the header is present;
- the response is not an SEC rate-limit or access-denied page;
- XML and XSD documents are well-formed;
- HTML contains recognizable HTML or Inline XBRL structure;
- the complete submission file contains SEC submission markers;
- the file can be reopened and produces the same checksum after it is written.

These checks protect file integrity. They do not require every issuer to use the same HTML or XBRL structure.

## Run manifest

Every ingestion attempt creates a new JSON manifest. A retry links to the previous run but does not rewrite it.

The manifest records:

- run ID, timestamps, code version, and terminal status;
- CIK, accession number, form, filing date, report period, and source index URL;
- the expected document inventory and classification of each item;
- request URL, attempt count, HTTP result, file size, checksum, and storage path;
- download and validation outcome for every document;
- structured error code and message when a step fails;
- final values for `source_complete` and `parser_ready`.

The manifest may be updated while the run is active. Once the run reaches `COMPLETE`, `PARTIAL`, or `FAILED`, it is immutable.

Application logs remain separate. Logs explain the sequence of events; the manifest is the machine-readable record of the run.

### Status values

```text
run_status:
RUNNING | COMPLETE | PARTIAL | FAILED

document_status:
NOT_STARTED | DOWNLOADED | VERIFIED | MISSING | SKIPPED_IDENTICAL | FAILED | CONTENT_CHANGE_DETECTED
```

For the current local publisher, `COMPLETE` means every inventory file and both required metadata files were verified and published. `PARTIAL` means all required files, including metadata, were verified and published, while an optional file is `MISSING` or `FAILED`. `FAILED` means the filing was not published and `source_complete` is false. `MISSING` records that no bytes were supplied by the caller; it does not imply a download attempt.

## Failure and retry behavior

One failed document must not discard files that were already verified. A filing with missing or failed required material remains in staging with a failure record. A failed optional file may be omitted from a `PARTIAL` publication only after its staged bytes are removed. This publisher preserves the staging directory but has no resume API.

Network timeouts, connection resets, and HTTP `429`, `500`, `502`, `503`, and `504` are retryable. Retries use exponential backoff with jitter and honor `Retry-After`. Invalid URLs, invalid hosts, and repeated content-validation failures are not retried indefinitely.

An HTTP `403` or recognized SEC access-block response stops the run rather than increasing request pressure. A `404` is normally permanent, although a newly published filing may receive one delayed retry to account for publication lag.

The process exits nonzero when either the run fails or the required Bronze set remains incomplete. Failure details must be visible in both the log and manifest; exceptions are never swallowed.

## SEC request policy

All requests use a configured user agent with a real contact address. The process refuses to start when this value is missing.

```text
User-Agent: <application-name> <contact-email>
Accept-Encoding: gzip, deflate
```

Version 1 uses one worker and a maximum rate of two requests per second. The client applies a 10-second connection timeout, a 30-second read timeout, and at most five attempts per request. These values are configuration, not hard-coded constants.

The client must stay within the SEC's [fair-access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), even if configured values are changed later.

## Acceptance criteria

Version 1 is complete when it can ingest a real filing without issuer-specific filenames and demonstrate the following behavior through automated tests:

- a complete filing reaches `COMPLETE` with both completion flags set;
- an empty or malformed response never reaches the canonical path;
- a transient SEC error is retried and can recover;
- retry exhaustion produces a visible document-level failure;
- rerunning identical content does not duplicate or overwrite data;
- changed content for the same document is quarantined;
- a missing exhibit and a missing parser dependency produce the correct completion flags;
- a partially successful run retains verified staged files and a failure record; resume support is a later increment;
- every retry creates a new manifest linked to the earlier run.

HTTP behavior is mocked in the test suite. A single controlled integration test uses a real SEC filing after the local suite passes.

Apple's Form 10-Q with accession `0000320193-26-000013` is the Version 1 integration fixture. The implementation must not contain Apple-specific discovery or document rules.

## Deferred work

The following decisions are intentionally deferred until the next part of the pipeline requires them:

- multi-filing discovery and scheduling;
- final XBRL parser and Silver schema;
- S3 bucket and object-versioning policy;
- Spark, Iceberg, and catalog design;
- orchestration, backfills, and operational control tables;
- monitoring backend and alert routing;
- website and serving layer.
