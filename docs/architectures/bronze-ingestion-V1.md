# Bronze: keeping a filing we can trust

Bronze saves the SEC's source files before we try to interpret any financial values. A filing is identified by the issuer's normalized CIK and its accession number. The accession prefix can belong to a filing agent, so it is not used as the issuer CIK. Amendments get their own accessions.

## What gets saved

For a selected filing, the ingester reads its SEC filing index once and keeps the exact index response alongside `discovery.json`. It inventories submitted documents, the linked complete-submission `.txt`, and any listed SEC Data Files. Submitted documents, the submission text, and the discovery evidence are required for a complete source archive. Data Files can be missing without pretending they were downloaded.

```text
<bronze root>/cik=<CIK>/accession=<accession>/
├── submitted/
├── submission-package/
├── sec-derived/
├── metadata/filing-index.html
├── metadata/discovery.json
└── manifests/run_id=<run-id>.json
```

`source_complete` answers whether the required archive is intact. `parser_ready` is a different check: exactly one verified Data Files entry must be described as an extracted XBRL instance document with an XML filename. A source-complete filing can still lack usable Silver input.

The manifest records each expected file, its outcome, byte count, SHA-256 when present, network attempts, and the run status. The original response bytes stay available; a checksum by itself is not proof that an SEC block page or an HTML error is the requested document.

The inventory and saved index come from the **same** response. Fetching the index again for evidence could leave us with one file list and a different set of bytes supposedly explaining it. Filenames come from the SEC inventory and are checked as safe local names; the ingester does not guess a missing primary document or construct a submission filename from the accession.

Verification is tailored to the kind of file. The request must target the expected filing on an allowed SEC host, succeed without a redirect, and return nonempty content. XML/XSD must be well formed; primary HTML needs recognizable document markup; the complete submission text needs SEC submission markers. Files are checked again after staging, because validation of an HTTP response does not prove the bytes on disk match it. A reported `Content-Length` is compared only when it describes the bytes the HTTP client exposes, since compressed transfer lengths can differ.

## Request and publication rules

The company run shares one HTTP client and pacer across discovery and all selected filings. Requests are spaced at least 0.5 seconds apart. The request layer uses 10-second connect and 30-second read timeouts, with at most three attempts for transient network errors, 429, and selected 5xx responses. It respects a valid `Retry-After`; a wait over 60 seconds stops the run rather than retrying early. A 403 or recognized access-block page also stops further SEC requests. Redirects are not followed. See the SEC's [fair-access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) for the identified User-Agent requirement.

Pacing belongs to the whole company run, not each file or filing, so starting the next filing cannot reset the request interval. Retries are for temporary transport or server conditions. A bad document name, invalid content, or an ordinary non-retryable HTTP error needs a recorded failure, not another request. The terminal conditions stop the run even if the affected file was optional: continuing after an access block would make the problem worse.

Downloaded bytes are checked for response and content problems, written to a sibling staging tree, then reopened for size and checksum verification. Publication moves a verified staging directory to the canonical accession path. A required-file failure leaves a failed manifest in staging and no new canonical filing. A failed optional Data File can produce a published `PARTIAL` filing. An existing canonical filing is not overwritten; the company coordinator verifies it before reuse.

Staging is on the same filesystem as the canonical destination so the final move can expose a complete directory. The manifest distinguishes required and optional files; otherwise a missing XBRL support file would be easy to confuse with an incomplete source archive. It also records the final reason and attempt count instead of losing those details in a generic pipeline exception.

| Bronze status | What it means |
| --- | --- |
| `COMPLETE` | Required and optional inventory files verified and published. |
| `PARTIAL` | Required source files verified and published; at least one optional Data File did not. |
| `FAILED` | Required verification or publication failed; the attempt is not a published source. |

Bronze does not pick financial facts or settle disagreements between filings. It keeps one canonical source directory per accession; changed SEC bytes under an existing accession are not silently substituted. Failed staging work is kept for diagnosis, not treated as published input. Silver takes over only after it verifies the Bronze manifest and chosen XML again.

The [Apple/Microsoft acceptance run](../validation/multi-company-acceptance-V1.md) checked four real filings in one shared root, then confirmed that a Microsoft rerun reused the same published source files without changing earlier evidence. The [offline CI check](../validation/ci-V1.md) exercises the source handoff with fictional filings and no SEC requests.
