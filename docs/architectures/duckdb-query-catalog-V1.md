# DuckDB catalog: one view of active Silver

Silver stores immutable Parquet versions per filing. The catalog turns the verified **active** versions into stable SQL names. It is derived state: the active pointers and their published files remain the source of truth.

A refresh scans a supplied Silver root, resolves each `active.json`, and checks the referenced `publication.json` and all three Parquet files. It checks identity, supported parser/schema versions, file names, schemas, row counts, sizes, hashes, unique fact IDs, and dimension links. A directory under `versions/` has no effect unless an active pointer selects it. Current catalog verification accepts parser revisions `1`, `2`, and `3` with schema `1`.

| Relation | What it holds |
| --- | --- |
| `silver.active_filings` | One verified active version per included CIK/accession, with source and activation evidence. |
| `silver.facts` | Accepted XBRL occurrences from those versions. |
| `silver.fact_dimensions` | Dimensions belonging to accepted occurrences. |
| `silver.rejected_facts` | Rejected numeric candidates from those versions. |
| `silver.catalog_failures` | Active pointers excluded from the latest refresh, with reasons. |

The row-level relations are external views over explicit Parquet file lists; Silver facts are not copied into DuckDB tables. The refresh builds the new file lists and failure records, replaces its tables and views in one DuckDB transaction, checks their counts and links, then commits. If no verified active filing remains, or the transaction fails, it leaves the previous usable catalog in place. With some good and some bad pointers, it returns `PARTIAL` and exposes the exclusions in `silver.catalog_failures` rather than hiding them.

## Why this is a catalog rather than another copy

The Silver publication is already a checked, versioned dataset. Copying its rows into another DuckDB fact table would create a second set of data to reconcile after each parser revision or failed run. The catalog stores the chosen file paths and active-filing evidence, then lets DuckDB read those Parquet files directly. `silver.active_filings` and `silver.catalog_failures` are ordinary tables because they describe the catalog snapshot; the three fact-level relations are views.

Those paths are built from verified pointers, not a recursive file search. The views disable Hive partition inference: folder names such as `cik=...` and `schema=...` are storage layout, not extra fact columns. Refresh requires the declared schema instead of using `union_by_name` to paper over incompatible versions with null columns. A new row schema therefore needs an explicit catalog change.

Suppose Apple has a good active version and Microsoft has a corrupt pointer. Refresh includes Apple, records Microsoft's path and failure reason, and returns `PARTIAL`. If neither can be verified, it refuses to publish an empty replacement. The transaction makes `active_filings`, `catalog_failures`, and all three views describe the same set of files; counts and fact-to-dimension links are checked before commit. Stored filing/fiscal metadata tables are separate and are not rebuilt from Parquet by this refresh.

```sh
uv run --locked python -m sec_edgar_lakehouse.catalog.silver_catalog_refresh \
  --silver-directory data/silver/sec/filings \
  --database data/query/sec_edgar.duckdb
```

The catalog refresh is explicit; publishing a new Silver version does not automatically update DuckDB. The company pipeline performs one refresh after processing its selected filings. A later company run against the same roots will include earlier active filings too.

DuckDB stores absolute paths to the verified Parquet files. If the repository or dataset moves, refresh the catalog from the new Silver root. The catalog does not choose a company total, calculate a metric, repair a bad Silver version, or reconcile amendments. Those are separate decisions in Gold or a future analysis layer. Keep pipeline writes and local database readers separate; this design assumes one writer and no concurrent pointer changes during refresh.

That last assumption matters: the transaction protects the **catalog definitions**, not the external files against a publisher changing them mid-query. Silver versions are immutable under normal operation, and pipeline stages run sequentially. The dashboard opens the database only after those writes finish. The catalog can be rebuilt from Silver, but the database file alone is not a portable archive of the Parquet data.

In the [Apple/Microsoft acceptance run](../validation/multi-company-acceptance-V1.md), the shared catalog held two active filings per company after the second run. Adding Microsoft did not remove Apple's Gold records or change its stored metadata; the rerun left the complete ordered catalog rows unchanged.

[`silver_catalog.py`](../../src/sec_edgar_lakehouse/catalog/silver_catalog.py) owns pointer discovery, verification, the DuckDB transaction, and post-replacement checks. [`silver_catalog_refresh.py`](../../src/sec_edgar_lakehouse/catalog/silver_catalog_refresh.py) is only its CLI. [Catalog tests](../../tests/catalog/) cover bad pointers, partial refresh, transaction rollback, schema and link checks, and supported parser versions. The catalog's key is `(cik, accession_number)`, not accession alone; the three Parquet-backed views retain the occurrence-level grain from Silver.
