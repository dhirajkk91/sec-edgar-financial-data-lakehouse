# SEC EDGAR DuckDB Query Catalog — Version 1

Silver already publishes verified Parquet files, but those files live under versioned paths. A person writing SQL should not need to find `active.json`, follow its pointer, and assemble a list of Parquet files for every filing. The DuckDB query catalog provides that layer.

The catalog is derived from published Silver data. It does not become a second source of truth. If the DuckDB file is removed, it can be rebuilt from the active Silver pointers and their published Parquet files.

## Outcome and scope

Version 1 builds one local DuckDB database over every verified active Silver filing under a supplied Silver root. It exposes a stable set of SQL relations that can be used for exploration and, later, as the input to Gold.

The first real input is the published Apple 10-K. The same refresh flow must also work when more companies and accessions are added. Adding a filing should require another Silver publication and a catalog refresh, not a new hardcoded SQL path.

This version has one catalog writer and local readers. It does not schedule refreshes, calculate financial metrics, copy Silver facts into DuckDB tables, or query cloud storage.

## Why DuckDB is here

Parquet remains the storage format for Silver. DuckDB supplies the SQL catalog and query engine.

This split is intentional:

- Silver versions stay immutable and independently verifiable.
- SQL users get stable names such as `silver.facts` instead of version-specific file paths.
- The large row-level datasets are not copied into another local store.
- The catalog can be recreated when active versions change or the repository moves.

The database file lives at a caller-supplied path such as `data/query/sec_edgar.duckdb`. Local data remains outside Git. The automatically created `main` schema is left alone; project relations live in the `silver` schema.

## Source of truth

Each filing publishes an active pointer at:

```text
data/silver/sec/filings/
└── cik=<10-digit-cik>/
    └── accession=<accession-number>/
        └── active.json
```

The catalog follows `active.json`. It never globs every Parquet file under `versions/`, because doing that would mix current and historical outputs.

For each pointer, refresh verifies the active publication before including it:

1. The CIK and accession directory names are valid and agree with the pointer.
2. The pointer stays inside the supplied Silver root and does not pass through a managed symlink.
3. The referenced version and `publication.json` exist at the declared relative paths.
4. The publication checksum matches the checksum recorded by `active.json`.
5. The publication identity agrees with the pointer.
6. All three Parquet files have the declared names, schemas, row counts, byte sizes, and SHA-256 checksums.
7. Fact identifiers are unique and every dimension points to an accepted fact.
8. The parser and schema versions are supported by this catalog build.

A directory under `versions/` has no effect on SQL unless a verified active pointer selects it.

## Query surface

Version 1 creates these relations:

| Relation | Type | One row means |
| --- | --- | --- |
| `silver.active_filings` | DuckDB table | One verified active Silver version included in the current catalog snapshot |
| `silver.facts` | External view | One accepted numeric fact occurrence from an active filing |
| `silver.fact_dimensions` | External view | One dimension attached to an accepted active fact |
| `silver.rejected_facts` | External view | One rejected numeric candidate from an active filing |
| `silver.catalog_failures` | DuckDB table | One active pointer that could not be safely included during the latest refresh |

`silver.active_filings` keeps the lineage needed to understand the snapshot. Its fields include the filing identity, source document and checksum, Silver status, parser and schema versions, active version path, publication path and checksum, activation run and time, and the published row counts.

`silver.catalog_failures` keeps the filing path and a readable failure reason. It must not invent an identity that could not be read safely. When the directory provides a valid CIK or accession component, the record may preserve it as observed path evidence.

The three row-level views preserve the Parquet schemas defined by Silver. They use explicit file lists and `hive_partitioning = false`. Directory components such as `cik=...`, `parser=...`, and `schema=...` are layout metadata, not extra columns to infer from the path.

Views require matching schema versions. Version 1 does not use `union_by_name` to hide incompatible files by filling missing columns with nulls. An unsupported active schema is a catalog failure for that filing.

## Refresh flow

The refresh command accepts a Silver filings root and a DuckDB database path. A typical local command will look like:

```bash
uv run python -m sec_edgar_lakehouse.silver_catalog_refresh \
  --silver-directory data/silver/sec/filings \
  --database data/query/sec_edgar.duckdb
```

Refresh follows this order:

1. Validate and resolve the supplied paths.
2. Find filing directories in a stable order.
3. Resolve and verify each `active.json` independently.
4. Build the active-filing records, Parquet file lists, and current failure records in memory.
5. Refuse the refresh if no verified active filing remains.
6. Start one DuckDB transaction.
7. Replace `active_filings`, `catalog_failures`, and the three external views.
8. Query the new relations to verify schemas, counts, fact identity, and dimension links.
9. Commit the transaction only after every catalog-level check passes.

If a database already exists and the transaction fails, its previous catalog remains available. When a new database cannot complete its first refresh, the command must not leave it looking like a usable catalog.

The catalog stores absolute Parquet paths so DuckDB can open the exact verified files. Moving the repository makes those paths stale; running refresh again rebuilds them for the new location.

## Filing-level failures

One bad active filing should not hide valid companies from SQL. Refresh handles filings independently:

| Refresh status | Meaning | Catalog behavior |
| --- | --- | --- |
| `COMPLETE` | Every discovered active pointer verified | Publish all active filings with an empty `catalog_failures` table |
| `PARTIAL` | At least one active filing verified and at least one failed verification | Publish the valid filings and record every excluded pointer in `catalog_failures` |
| `FAILED` | No verified active filing remains, or the DuckDB transaction or final verification fails | Keep the previous catalog unchanged |

A `PARTIAL` result is visible in command output and through `silver.catalog_failures`. Consumers must not be told that the snapshot is complete when a filing was excluded.

Missing `active.json` does not make a historical version current. A filing directory with only failed runs or an orphaned version contributes no financial rows. Publication and recovery remain the responsibility of Silver, not the query catalog.

## Consistency and idempotency

The catalog represents a snapshot of active pointers observed during one refresh. Its metadata tables and external views change together in a DuckDB transaction. Readers must not see new fact paths paired with old active-filing metadata.

Running refresh again with the same active versions produces the same queryable rows. It may replace the derived catalog definitions, but it does not rewrite Parquet files, publication records, active pointers, or financial facts.

Version 1 assumes no Silver publisher changes an active pointer while refresh is validating it. Scheduled execution will later order Silver publication before catalog refresh. Multi-writer coordination is outside the local Version 1 design.

The DuckDB file is derived state, while these remain authoritative:

```text
active.json
    -> publication.json
        -> facts.parquet
        -> fact_dimensions.parquet
        -> rejected_facts.parquet
```

## SQL usage

Consumers query the stable relations rather than file paths:

```sql
SELECT
    cik,
    accession_number,
    concept_local_name,
    value_decimal,
    period_start,
    period_end,
    period_instant
FROM silver.facts
WHERE concept_local_name =
    'RevenueFromContractWithCustomerExcludingAssessedTax';
```

Gold may join `silver.facts` to `silver.fact_dimensions` and `silver.active_filings`, but Gold must still decide which reported occurrence represents a requested business metric. The query catalog does not label a dimensionless fact as a consolidated total, convert year-to-date values into quarters, or deduplicate facts that only look alike.

## Boundaries

Version 1 does not provide:

- Gold metric selection or business calculations;
- automatic refresh after Silver publication;
- a network database service or concurrent catalog writers;
- cloud object storage, a remote metastore, or access control;
- cross-schema migration between incompatible Silver versions;
- automatic repair of a broken active pointer or Silver publication;
- a guarantee that the snapshot stays current after an active pointer changes.

Airflow can invoke refresh after multi-filing processing exists. A cloud version can replace the local database catalog with object storage and a shared catalog while keeping the same active-only consumption rule.

## Version 1 acceptance

Version 1 is complete when automated tests and a controlled local run show that:

- the Apple active Silver version appears through all three row-level relations with counts `963`, `591`, and `0`;
- adding another active filing makes both filings queryable without hardcoded company-specific SQL;
- historical inactive versions never appear in the views;
- an equivalent refresh does not change or duplicate financial rows;
- a corrupt active pointer or version is excluded and recorded while other verified filings remain queryable;
- zero valid active filings or a catalog verification failure preserves the prior database state;
- all metadata tables and external views move to the same snapshot in one transaction;
- every dimension in the catalog still points to a fact in the same active snapshot;
- the DuckDB file can be deleted and rebuilt from Silver without modifying Silver data.

Passing these checks provides a stable local SQL handoff from Silver to Gold. It does not claim that the catalog is a distributed warehouse or that the active facts have already been reduced to business metrics.
