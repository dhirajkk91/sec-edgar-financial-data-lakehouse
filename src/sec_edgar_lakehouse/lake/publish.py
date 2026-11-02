"""Publish the pipeline's output to the lake: S3 objects plus Iceberg tables.

    sec-edgar publish-lake --data-root data/lake --lake s3://my-bucket/sec-edgar
    sec-edgar publish-lake --data-root data/lake --lake file:///tmp/sec-lake

The pipeline computes on one machine; the lake is where its output lives for
everyone else. Each run does four things, in this order:

1. **Bronze archive.** New Bronze filings are gzipped and uploaded once, under
   ``bronze/cik=…/accession=…/``. Bronze is write-once, so a ledger of uploaded
   filings is enough to skip them next time. Each object keeps the original
   file's SHA-256 in its metadata, so the archive can rebuild Bronze exactly.
2. **Silver tables.** ``sec_silver.facts``, ``fact_dimensions`` and
   ``rejected_facts`` are Iceberg tables partitioned by CIK. Only filings whose
   active Silver version changed since the last publish are written: new ones are
   appended, re-parsed ones are replaced, in one commit per table.
3. **Gold tables.** The seven Gold tables are copied through the demo export,
   which rewrites local paths and refuses to publish home folders or email
   addresses, then each ``sec_gold`` table is overwritten in one commit. Every
   publish is an Iceberg snapshot, so earlier nights stay queryable.
4. **Dashboard snapshot.** The same Gold-only DuckDB file goes to
   ``serving/sec_edgar_gold.duckdb`` with its manifest, for the hosted dashboard.

With ``s3://`` the catalog is AWS Glue and Athena can query every table. With
``file://`` a SQLite catalog and a local folder stand in for both, so the whole
publish runs offline, which is what the tests use.
"""

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import warnings
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import duckdb

from sec_edgar_lakehouse.dashboard.demo_export import (
    DEMO_TABLES,
    DemoExportError,
    export_demo_database,
)

SILVER_NAMESPACE = "sec_silver"
GOLD_NAMESPACE = "sec_gold"
SILVER_TABLES = ("facts", "fact_dimensions", "rejected_facts")
SNAPSHOT_KEY = "serving/sec_edgar_gold.duckdb"
# Filings per Iceberg commit during a backfill; keeps each Arrow batch small.
SILVER_BATCH = 200


class LakePublishError(Exception):
    """The lake target or the local inputs could not be used."""


class ObjectStore(Protocol):
    """The two object operations the publish needs."""

    def put_file(self, key: str, path: Path, metadata: dict[str, str]) -> None: ...

    def describe(self) -> str: ...


@dataclass(frozen=True, slots=True)
class LocalStore:
    """A folder that stands in for a bucket."""

    root: Path

    def put_file(self, key: str, path: Path, metadata: dict[str, str]) -> None:
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        destination.with_name(destination.name + ".metadata.json").write_text(
            json.dumps(metadata, sort_keys=True) + "\n"
        )

    def describe(self) -> str:
        return f"file://{self.root}"


@dataclass(frozen=True, slots=True)
class S3Store:
    """A bucket prefix, written with boto3 and the standard AWS credential chain."""

    bucket: str
    prefix: str
    client: Any

    def put_file(self, key: str, path: Path, metadata: dict[str, str]) -> None:
        self.client.upload_file(
            str(path),
            self.bucket,
            f"{self.prefix}/{key}" if self.prefix else key,
            ExtraArgs={"Metadata": metadata},
        )

    def describe(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"


@dataclass(frozen=True, slots=True)
class LakeTarget:
    """Where objects go and which Iceberg catalog records the tables."""

    store: ObjectStore
    catalog: Any
    warehouse: str


def open_target(lake: str, *, region: str | None = None) -> LakeTarget:
    """``s3://bucket/prefix`` uses S3 and Glue; ``file:///path`` stays on disk."""
    from pyiceberg.catalog import load_catalog
    from pyiceberg.catalog.sql import SqlCatalog

    parsed = urlparse(lake)
    if parsed.scheme == "file":
        base = Path(parsed.path).resolve()
        (base / "warehouse").mkdir(parents=True, exist_ok=True)
        catalog: Any = SqlCatalog(
            "sec_edgar",
            uri=f"sqlite:///{base / 'catalog.db'}",
            warehouse=f"file://{base / 'warehouse'}",
        )
        return LakeTarget(
            LocalStore(base / "objects"), catalog, f"file://{base / 'warehouse'}"
        )
    if parsed.scheme == "s3" and parsed.netloc:
        import boto3  # type: ignore[import-untyped]

        region = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )
        if not region:
            raise LakePublishError("Set --region or AWS_REGION for an s3:// lake")
        prefix = parsed.path.strip("/")
        warehouse = f"s3://{parsed.netloc}/{prefix + '/' if prefix else ''}warehouse"
        catalog = load_catalog(
            "sec_edgar",
            **{
                "type": "glue",
                "glue.region": region,
                "s3.region": region,
                "warehouse": warehouse,
            },
        )
        client = boto3.client("s3", region_name=region)
        return LakeTarget(S3Store(parsed.netloc, prefix, client), catalog, warehouse)
    raise LakePublishError(
        f"Lake must be s3://bucket/prefix or file:///path, not {lake!r}"
    )


# ---------------------------------------------------------------- ledger


def _read_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1", "bronze": [], "silver": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "1":
        raise LakePublishError(f"Unrecognised lake ledger: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", delete=False, encoding="utf-8"
    ) as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stream.name, path)


# ---------------------------------------------------------------- Bronze


def _published_filings(bronze: Path) -> Iterator[Path]:
    """Canonical filing folders; staging folders live outside ``bronze``."""
    if not bronze.is_dir():
        return
    for company in sorted(bronze.glob("cik=*")):
        for filing in sorted(company.glob("accession=*")):
            if (
                filing.is_dir()
                and not filing.is_symlink()
                and (filing / "manifests").is_dir()
            ):
                yield filing


def publish_bronze(root: Path, target: LakeTarget, ledger: dict[str, Any]) -> int:
    """Gzip and upload each Bronze filing once; returns how many were uploaded."""
    done = set(ledger["bronze"])
    uploaded = 0
    with tempfile.TemporaryDirectory() as scratch:
        for filing in _published_filings(root / "bronze"):
            relative = filing.relative_to(root).as_posix()
            if relative in done:
                continue
            for path in sorted(p for p in filing.rglob("*") if p.is_file()):
                content = path.read_bytes()
                packed = Path(scratch) / "object.gz"
                # mtime=0 makes the gzip bytes depend only on the content.
                with gzip.GzipFile(packed, "wb", mtime=0) as stream:
                    stream.write(content)
                target.store.put_file(
                    f"{path.relative_to(root).as_posix()}.gz",
                    packed,
                    {
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size-bytes": str(len(content)),
                    },
                )
            ledger["bronze"].append(relative)
            done.add(relative)
            uploaded += 1
    ledger["bronze"].sort()
    return uploaded


# ---------------------------------------------------------------- Iceberg helpers


def _ensure_namespace(catalog: Any, namespace: str) -> None:
    """Use the namespace if it exists; on AWS Terraform creates it, so no create rights."""
    from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchNamespaceError

    try:
        catalog.load_namespace_properties(namespace)
        return
    except NoSuchNamespaceError:
        pass
    try:
        catalog.create_namespace(namespace)
    except NamespaceAlreadyExistsError:
        pass


def _table(
    target: LakeTarget, namespace: str, name: str, schema: Any, partition_by: str | None
) -> Any:
    """Load the table, or create it; new columns from the data are added either way."""
    identifier = (namespace, name)
    if not target.catalog.table_exists(identifier):
        table = target.catalog.create_table(
            identifier,
            schema=schema,
            location=f"{target.warehouse}/{namespace}/{name}",
        )
        if partition_by:
            with table.update_spec() as spec:
                spec.add_identity(partition_by)
        return table
    table = target.catalog.load_table(identifier)
    with table.update_schema() as update:
        update.union_by_name(schema)
    return table


def _connect(database: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(str(database), read_only=True)
    # Iceberg timestamps with a zone must be UTC.
    connection.execute("SET TimeZone = 'UTC'")
    return connection


# ---------------------------------------------------------------- Silver


def _active_versions(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, tuple[str, str]]:
    """accession -> (activation run ID, version folder) for every active Silver filing."""
    rows = connection.execute(
        "SELECT accession_number, activation_run_id, version_path "
        "FROM silver.active_filings ORDER BY accession_number"
    ).fetchall()
    return {accession: (run_id, path) for accession, run_id, path in rows}


def _silver_batch(
    connection: duckdb.DuckDBPyConnection, name: str, versions: Sequence[str]
) -> Any:
    """One Silver table's rows for these version folders, as an Arrow table.

    Dimension rows carry no filing IDs in Silver; they get them from their fact,
    so the lake table can be partitioned and replaced by filing like the others.
    """

    def paths(table: str) -> str:
        return ", ".join(
            "'" + f"{version}/{table}.parquet".replace("'", "''") + "'"
            for version in versions
        )

    files = paths(name)
    if name == "fact_dimensions":
        facts = paths("facts")
        query = (
            "SELECT f.cik, f.accession_number, d.* "
            f"FROM read_parquet([{files}], hive_partitioning=false) AS d "
            f"JOIN (SELECT source_occurrence_id, cik, accession_number FROM "
            f"read_parquet([{facts}], hive_partitioning=false)) AS f "
            "USING (source_occurrence_id)"
        )
    else:
        query = f"SELECT * FROM read_parquet([{files}], hive_partitioning=false)"
    return connection.execute(query).to_arrow_table()


def publish_silver(
    database: Path, target: LakeTarget, ledger: dict[str, Any]
) -> dict[str, int]:
    """Write Silver for filings whose active version changed; returns rows per table."""
    published: dict[str, str] = ledger["silver"]
    rows = dict.fromkeys(SILVER_TABLES, 0)
    with _connect(database) as connection:
        active = _active_versions(connection)
        changed = [a for a, (run_id, _) in active.items() if published.get(a) != run_id]
        if not changed:
            return rows
        _ensure_namespace(target.catalog, SILVER_NAMESPACE)
        for start in range(0, len(changed), SILVER_BATCH):
            batch = changed[start : start + SILVER_BATCH]
            versions = [active[a][1] for a in batch]
            replaced = [a for a in batch if a in published]
            for name in SILVER_TABLES:
                data = _silver_batch(connection, name, versions)
                table = _table(target, SILVER_NAMESPACE, name, data.schema, "cik")
                with table.transaction() as transaction, warnings.catch_warnings():
                    # A re-parsed filing can have no rows in a table, e.g. no rejections.
                    warnings.filterwarnings("ignore", "Delete operation did not match")
                    if replaced:
                        # Accession numbers are digits and dashes, safe to quote.
                        listed = ", ".join(f"'{a}'" for a in replaced)
                        transaction.delete(f"accession_number IN ({listed})")
                    if data.num_rows:
                        transaction.append(data)
                rows[name] += data.num_rows
            for accession in batch:
                published[accession] = active[accession][0]
    return rows


# ---------------------------------------------------------------- Gold and snapshot


def publish_gold(snapshot: Path, target: LakeTarget) -> dict[str, int]:
    """Overwrite each Gold table from the checked snapshot, one commit per table."""
    _ensure_namespace(target.catalog, GOLD_NAMESPACE)
    rows = {}
    with _connect(snapshot) as connection:
        for name in DEMO_TABLES:
            data = connection.execute(f"SELECT * FROM gold.{name}").to_arrow_table()
            table = _table(target, GOLD_NAMESPACE, name, data.schema, None)
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", "Delete operation did not match")
                table.overwrite(data)
            rows[name] = data.num_rows
    return rows


def run_publish(
    root: Path, target: LakeTarget, *, skip_bronze: bool = False
) -> dict[str, Any]:
    """Publish Bronze, Silver, Gold and the dashboard snapshot; save the ledger after each."""
    root = root.resolve()
    database = root / "query" / "sec_edgar.duckdb"
    if not database.is_file():
        raise LakePublishError(f"No catalog database at {database}; run sync first")
    work = root / "lake"
    ledger_path = work / "ledger.json"
    ledger = _read_ledger(ledger_path)
    started = datetime.now(UTC)

    bronze = 0 if skip_bronze else publish_bronze(root, target, ledger)
    _write_json(ledger_path, ledger)
    silver = publish_silver(database, target, ledger)
    _write_json(ledger_path, ledger)

    snapshot = work / "sec_edgar_gold.duckdb"
    try:
        exported = export_demo_database(
            source=database, destination=snapshot, replace=True
        )
    except DemoExportError as exc:
        raise LakePublishError(f"Gold snapshot failed its checks: {exc}") from exc
    gold = publish_gold(snapshot, target)
    target.store.put_file(SNAPSHOT_KEY, snapshot, {"sha256": exported.sha256})
    target.store.put_file(
        SNAPSHOT_KEY.removesuffix(".duckdb") + ".json",
        exported.manifest,
        {"sha256": hashlib.sha256(exported.manifest.read_bytes()).hexdigest()},
    )
    summary = {
        "schema_version": "1",
        "status": "COMPLETE",
        "lake": target.store.describe(),
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "bronze_filings_uploaded": bronze,
        "silver_rows_written": silver,
        "gold_rows": gold,
        "snapshot_sha256": exported.sha256,
    }
    _write_json(work / "publish-runs" / "latest.json", summary)
    # Run summaries go to the lake too, as public-safe evidence of the last night.
    sync_summary = root / "sync-runs" / "latest.json"
    if sync_summary.is_file():
        target.store.put_file("runs/latest-sync.json", sync_summary, {})
    target.store.put_file(
        "runs/latest-publish.json", work / "publish-runs" / "latest.json", {}
    )
    return summary


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument(
        "--lake",
        default=os.environ.get("SEC_EDGAR_LAKE"),
        help="s3://bucket/prefix or file:///path (default: SEC_EDGAR_LAKE)",
    )
    parser.add_argument("--region", help="AWS region (default: AWS_REGION)")
    parser.add_argument(
        "--skip-bronze", action="store_true", help="Publish tables only"
    )
    args = parser.parse_args(arguments)
    if not args.lake:
        parser.error("--lake or SEC_EDGAR_LAKE is required")
    try:
        target = open_target(args.lake, region=args.region)
        summary = run_publish(args.data_root, target, skip_bronze=args.skip_bronze)
    except ImportError as exc:
        print(
            f"FAILED: lake packages are missing ({exc}); install the 'lake' group",
            file=sys.stderr,
        )
        return 1
    except (LakePublishError, OSError, ValueError, duckdb.Error) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    silver = sum(summary["silver_rows_written"].values())
    print(
        f"Lake {summary['lake']}: {summary['bronze_filings_uploaded']} Bronze filings, "
        f"{silver:,} Silver rows, {sum(summary['gold_rows'].values()):,} Gold rows, "
        f"snapshot {summary['snapshot_sha256'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
