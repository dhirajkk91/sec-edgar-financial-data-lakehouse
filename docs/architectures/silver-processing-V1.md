# Silver: facts before financial decisions

Silver reads the extracted XBRL instance from a published Bronze filing. It keeps numeric fact **occurrences**, not one chosen value per concept. The same revenue concept can appear for a quarter, year to date, a comparative year, or a segment; even identical-looking occurrences stay separate here. Gold makes the later business selection.

## Input and identity

Before parsing, Silver checks the canonical Bronze path, manifest status, discovery record, and the single verified extracted-instance XML. It reopens that file and matches its size and SHA-256 to the manifest. A staged or changed file cannot become Silver input just because its filename looks right.

A `source_occurrence_id` is deterministic from the issuer CIK, accession, document name and checksum, and the item's position under the XML root. It does not depend on the processing run ID. That lets a rerun point to the same source occurrence without collapsing separate facts. The parser also keeps the XBRL concept, context, entity, unit, dimensions, period boundaries, raw value, and reported `decimals`/`precision` attributes.

Position is counted before deciding whether a root item is an accepted numeric fact. That matters when a parser revision starts recognizing a previously missed item: later source IDs should still point to the same positions. The concept namespace is kept with the local name so a company extension named `Assets` does not silently become US-GAAP `Assets`. Namespace scope is retained while parsing units and typed dimensions, where a prefix may be redefined inside the XML.

The parser treats a root-level item as a numeric candidate when it has a `unitRef`, `decimals`, or `precision` attribute, even if that attribute is empty. It also recognizes a small set of known US-GAAP and DEI numeric concepts by **namespace and name**, so a missing `unitRef` on one of those facts becomes an explicit rejection. Numeric-looking text or a familiar name in a company extension is not enough to guess that an otherwise unidentified item is numeric. This is a bounded recognition policy, not a full taxonomy resolver.

Missing units and other fact-level problems become rejected occurrences when the remaining document can be read safely. Broken shared structure, malformed XML, DTD/entity declarations, or an unidentifiable partial parse fail the extraction. The XML parser enforces DTD rejection after decoding, so UTF-8 and UTF-16 inputs follow the same rule; it makes no external requests.

That difference between a rejected **item** and a failed **filing** is intentional. A bad `contextRef` on one identifiable fact should not erase the other facts; a broken context table or interrupted parse could make all later results unreliable, so Silver publishes nothing new. Each candidate ends up either accepted or rejected, and a dimension row must point to an accepted fact.

## Output and publication

| Output | Grain and purpose |
| --- | --- |
| `facts.parquet` | One accepted numeric occurrence, with source, context, period, unit, and exact value. |
| `fact_dimensions.parquet` | One dimension on one accepted occurrence. |
| `rejected_facts.parquet` | One identifiable candidate excluded with a reason and whatever raw evidence was readable. |
| `runs/run_id=<id>.json` | One attempt, including failed and skipped attempts. |

Values are converted through Python `Decimal` and stored as `DECIMAL(38,18)`. A source value that cannot fit exactly is rejected, never rounded or passed through a float. Nil is null, not zero. A fact with no parsed dimensions is not automatically a consolidated company total.

For example, a 10-Q may contain revenue of 130 for its latest three months, 230 for six months year to date, and 40 for one segment. Silver keeps all three with their actual contexts. Adding them together, or choosing 230 just because it appears later in the XML, would answer a different question. Gold uses filing metadata, period, unit, dimensions, and agreement before it selects a reported metric.

Silver reconciles candidate, accepted, and rejected counts and checks every dimension link. It writes the three Parquet files into a staged version, verifies schema, rows, links, sizes, and checksums, then publishes the version and updates `active.json`. The active pointer changes only after the version has passed verification. Readers follow that pointer, not every directory under `versions/`.

```text
<silver root>/cik=<CIK>/accession=<accession>/
├── active.json
├── runs/run_id=<run-id>.json
└── versions/document=<XML>/source_sha256=<hash>/parser=<version>/schema=<version>/
    ├── facts.parquet
    ├── fact_dimensions.parquet
    ├── rejected_facts.parquet
    └── publication.json
```

`COMPLETE` means all identified candidates were accepted and at least one non-nil numeric fact exists. `PARTIAL` means useful facts were published with identifiable rejections. `FAILED` publishes no new active version; an earlier active pointer remains in place. `SKIPPED` records a new attempt when the same source, parser, and schema version already have verified output. A matching inactive version is not silently made active, except for the publication workflow's documented recovery of a failed activation.

The version key excludes the run ID. A rerun checks an existing version's files and publication evidence before reuse, then writes a separate attempt record. If a prior run moved a verified version into place but failed before activation, recovery can activate that same version after checking the earlier failure evidence. An arbitrary inactive historical version is not promoted just because its name matches. If writing the run record fails **after** the pointer changed, the activation is not rolled back; the failure is reported with the pointer left as it actually is.

The current parser is revision `3`, with row schema `1`. Revision 2 began recognizing certain known US-GAAP and DEI numeric concepts without `unitRef`; revision 3 also rejects nested identifier/date elements and unexpected text in context wrappers. Parser-1 and parser-2 versions remain historical evidence, and the catalog supports all three revisions. Reprocess a filing to publish its corrected parser-3 version. This is local, single-writer publication, not a transaction across filesystems or a guarantee against power loss.

The [two-company acceptance run](../validation/multi-company-acceptance-V1.md) checked active pointers, version files, and occurrence ownership across 4,265 accepted facts. Its Microsoft rerun kept the earlier active versions and pointers byte-for-byte unchanged.
