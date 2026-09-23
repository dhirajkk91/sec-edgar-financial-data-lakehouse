# SEC EDGAR Bronze Ingestion — Version 1

Version 1 ingests one SEC filing at a time into a local Bronze archive. Bronze keeps the source bytes and the evidence needed to explain which files were expected, which were received, and whether they were verified. Financial facts and analysis tables belong in Silver and Gold.

## Scope

The caller supplies a CIK and accession number. The pipeline reads the SEC filing index, builds an inventory, downloads the listed artifacts, and publishes a verified filing with a run manifest. It runs with one worker and does not schedule or discover filings across companies.

A published filing is a trustworthy input for later processing. Publication does not mean the filing has already been parsed into financial facts.

## Filing identity

| Item | Key |
| --- | --- |
| Filer | Zero-padded, 10-digit CIK |
| Filing | Accession number |
| Document | Accession number + document name |

The accession number's first ten digits can identify a filing agent rather than the filer; they are not required to match the CIK. Ticker, EIN, filing date, and report period are metadata, not filing keys. An amended filing has its own accession number and does not replace the original.

Each stored file also has a SHA-256 checksum. Version 1 records the observed bytes but does not decide whether a later response with the same document name should supersede them.

## Source inventory

Discovery uses one SEC filing-index response. The exact response bytes are retained at `metadata/filing-index.html`; `metadata/discovery.json` records the index URL, UTC retrieval time, and the ordered inventory produced from those same bytes. Both metadata files are required for publication.

| Artifact | Version 1 handling |
| --- | --- |
| Files in the SEC Document Format Files table | Required; preserve the listed documents, including the primary filing and exhibits |
| Complete submission `.txt` | Required; use its linked filename, not a guessed name |
| SEC Data Files table | Optional for source completeness; preserve listed XBRL support files when available |
| Viewer-only resources outside the submission inventory | Ignore unless listed as submitted documents |
| Unknown SEC document types in the submission inventory | Preserve without guessing their meaning |

Filenames come from the index and must be safe local filenames. A missing or malformed required row is a discovery failure; the pipeline must not guess a replacement document.

## Completeness and parser readiness

The run reports two separate booleans:

- `source_complete`: every submitted document, the complete submission `.txt`, and both discovery metadata files are verified.
- `parser_ready`: exactly one Data Files entry described by the SEC as `EXTRACTED XBRL INSTANCE DOCUMENT` has an XML filename and a verified, well-formed staged file. This is the first Silver parser's input contract.

SEC-derived files can be optional for `source_complete` while still being needed by the parser. Readiness requires both the SEC description and XML filename; an arbitrary XML file does not qualify. A filing may be source-complete but not parser-ready. If a required submitted exhibit fails while the extracted instance is verified, the run is still unsuccessful as a Bronze archive.

## Ingestion and publication

1. Validate the filing reference and User-Agent, then fetch the index.
2. Parse the index and build the expected inventory. Retain the exact index bytes and normalized discovery record.
3. Download the inventory files with one shared client and controlled request pacing.
4. Validate each response, then write the bytes into a run-specific staging directory. Reopen staged files to check their byte counts and SHA-256 hashes.
5. Record each artifact's outcome and the completion flags in the run manifest.
6. Publish the staged filing at its canonical path only when every required artifact, including discovery metadata, verifies. Leave failed runs in staging with a failure manifest and an operator-visible error.

No download writes directly to a canonical file. Optional SEC Data File failures may produce a published `PARTIAL` filing; required-file or metadata failures prevent publication. The existing canonical filing directory is never overwritten in Version 1.

## Storage layout

```text
data/bronze/sec/filings/
└── cik=<10-digit-cik>/
    └── accession=<accession-number>/
        ├── submitted/
        ├── submission-package/
        ├── sec-derived/
        ├── metadata/
        │   ├── filing-index.html
        │   └── discovery.json
        └── manifests/
            └── run_id=<run-id>.json
```

The canonical path is based on filing identity, not the day the job ran. Staging stays separate so a failed or interrupted run cannot appear as a published filing.

## Response checks

HTTP success and a matching SHA-256 checksum do not establish that the response is the expected document. Before publication, Version 1 checks:

- the request targets the expected filing on an allowed SEC host; redirects are not accepted;
- the response is successful and nonempty; compare its byte count with `Content-Length` only when that header describes the bytes exposed by the HTTP client (compressed transfer lengths may differ);
- the response is not an identifiable SEC access-block or rate-limit page;
- XML and XSD files are well-formed; primary HTML has recognizable document markup; the complete submission `.txt` has SEC submission markers;
- the bytes written to staging can be reopened and match the recorded size and SHA-256 hash.

These checks must not assume issuer-specific document names or a fixed set of HTML columns. A file that fails validation is recorded as failed, not promoted as a verified source artifact.

## SEC requests and failures

The client declares an application name and reachable contact address in its User-Agent. Version 1 uses one worker, no more than two requests per second, a 10-second connection timeout, and a 30-second read timeout. Requests must follow the SEC's [fair-access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

Transient connection errors, timeouts, HTTP `429`, and HTTP `500`, `502`, `503`, and `504` receive a bounded retry of at most three total attempts per request. Respect `Retry-After` when present. An access-block response or HTTP `403` stops requests for that run; invalid URLs, unsafe names, and content-validation failures are not retried as though they were transient network errors. Record the attempt count and final reason for failure.

If staging has started, a failed run keeps its staged files and failure manifest for inspection. An index request or discovery failure before staging raises a visible error without creating a canonical filing. Version 1 does not resume staging automatically; the caller must not mistake retained staged bytes for published Bronze data.

## Manifest and statuses

Each staged publication attempt has a unique JSON manifest recording its filing identity, run ID, source index URL and retrieval time, terminal status, `source_complete`, `parser_ready`, and one entry for every expected document and required metadata artifact. File entries record section, name, required flag, outcome, stored size and checksum when available, and an error reason when applicable. Network attempts are recorded without inventing HTTP details for failures that happened in storage.

| Run status | Meaning |
| --- | --- |
| `COMPLETE` | Every expected artifact, including optional Data Files, verified and was published |
| `PARTIAL` | Required artifacts verified and were published; at least one optional Data File is missing or failed |
| `FAILED` | A required artifact or publication failed; no canonical filing was published |

File-level statuses include `VERIFIED`, `MISSING`, and `FAILED`. `MISSING` means no bytes were provided for that inventory item; it does not imply an HTTP request was attempted. A final manifest is not rewritten after the run ends. Errors must identify the failed item and make the staging location available to the operator.

## Version 1 acceptance

Version 1 is complete when automated tests and one controlled SEC run show that:

- a real filing can reach `COMPLETE` without issuer-specific document rules; the stored index is the same response used for discovery, and all published artifacts have verified sizes and hashes;
- optional Data File failures can yield `PARTIAL` with `source_complete=true`; required document or metadata failures yield `FAILED` without a canonical filing;
- `parser_ready` follows the declared parser inputs independently of `source_complete`;
- an empty, malformed, or recognizable access-block response cannot be published as a verified artifact;
- a transient request can recover within the retry limit, while an exhausted retry or HTTP `403` produces a visible failure;
- a run against an existing canonical path refuses to overwrite it.


## After Version 1

Before scheduled or multi-company ingestion, add safe reruns for identical content, detection and quarantine of changed content, resumption of verified staging work, and linked attempt history. Those behaviors are not claimed by Version 1. Silver parsing and Gold financial models can begin after the local Version 1 source contract is met. Cloud storage, orchestration, backfills, monitoring, and a dashboard are separate later work.
