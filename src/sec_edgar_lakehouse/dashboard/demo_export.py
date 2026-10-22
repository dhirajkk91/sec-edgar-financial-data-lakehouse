"""Build a small, portable demo database for the hosted dashboard.

The dashboard reads only five Gold tables. This copies exactly those tables from
a built database into a new DuckDB file. Silver views are not copied, so the
file works on any machine. Absolute local paths are rewritten to paths relative
to the data root, and the export refuses to finish if any absolute path is left.
"""

import argparse
import contextlib
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from sec_edgar_lakehouse.dashboard.dashboard_read import (
    DashboardReadError,
    load_company_snapshot,
    load_dashboard_snapshot,
)

DEMO_TABLES = (
    "dim_filings",
    "fct_financial_metrics",
    "metric_quality_issues",
    "dim_companies",
    "fct_company_financials",
)
# Silver publication paths: keep the part from the layer folder onward.
PATH_COLUMNS = {"dim_filings": ("version_path", "publication_path")}


class DemoExportError(Exception):
    """The source, destination or exported content failed a check."""


@dataclass(frozen=True, slots=True)
class DemoExportResult:
    """Where the demo database was written, its checksum and its row counts."""

    destination: Path
    manifest: Path
    sha256: str
    size_bytes: int
    row_counts: dict[str, int]


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _check_source(source: Path) -> None:
    """Each demo table must be a stored table; a view would still need Silver."""
    with duckdb.connect(str(source), read_only=True) as connection:
        for table in DEMO_TABLES:
            kind = connection.execute(
                "SELECT table_type FROM information_schema.tables "
                "WHERE table_schema='gold' AND table_name=?",
                [table],
            ).fetchone()
            if kind is None:
                raise DemoExportError(f"Source has no gold.{table}; build Gold first")
            if kind[0] != "BASE TABLE":
                raise DemoExportError(
                    f"gold.{table} is a {kind[0]}; the demo needs stored Gold tables"
                )


def _copy_tables(
    connection: duckdb.DuckDBPyConnection, source: Path, data_root: str
) -> None:
    connection.execute(f"ATTACH {_quote(source)} AS source_db (READ_ONLY)")
    connection.execute("CREATE SCHEMA gold")
    for table in DEMO_TABLES:
        # '<data root>/silver/cik=…' becomes 'silver/cik=…'. A path recorded under
        # another root keeps everything from its first layer folder onward.
        rewrites = ", ".join(
            f"CASE WHEN starts_with({column}, $root) "
            f"THEN substr({column}, length($root) + 1) "
            f"ELSE regexp_replace({column}, '^.*?/((bronze|silver|runs)/)', '\\1') "
            f"END AS {column}"
            for column in PATH_COLUMNS.get(table, ())
        )
        projection = f"* REPLACE ({rewrites})" if rewrites else "*"
        connection.execute(
            f"CREATE TABLE gold.{table} AS SELECT {projection} "
            f"FROM source_db.gold.{table}",
            {"root": data_root + "/"} if rewrites else None,
        )
    connection.execute("DETACH source_db")


# Home, temp and mount folders on macOS, Linux and Windows, at the start of a
# value or after a separator. URLs such as 'http://fasb.org/us-gaap' do not match.
_LOCAL_PATH = (
    r"(^|[\s\[,'\"(=])"
    r"(/(Users|home|root|private|var|tmp|mnt|Volumes|opt|sessions)/|[A-Za-z]:[\\/])"
)
_EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"


def _check_published_text(
    connection: duckdb.DuckDBPyConnection, forbidden: Sequence[str]
) -> None:
    """Fail if any text value holds a local path, an email address or a given string."""
    columns = connection.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema='gold' AND data_type IN ('VARCHAR', 'VARCHAR[]')"
    ).fetchall()
    for table, column in columns:
        value = f'"{column}"::VARCHAR'
        checks = [
            ("regexp_matches(value, ?)", _LOCAL_PATH, "a local path"),
            ("regexp_matches(value, ?)", _EMAIL, "an email address"),
            *(
                ("contains(value, ?)", text, "a local folder name")
                for text in forbidden
            ),
        ]
        if column in PATH_COLUMNS.get(table, ()):
            checks.append(
                (
                    "NOT regexp_matches(value, ?)",
                    "^(bronze|silver|runs)/",
                    "a non-relative path",
                )
            )
        for condition, parameter, label in checks:
            leaked = connection.execute(
                f'SELECT 1 FROM (SELECT {value} AS value FROM gold."{table}") '
                f"WHERE {condition} LIMIT 1",
                [parameter],
            ).fetchone()
            if leaked is not None:
                raise DemoExportError(
                    f"gold.{table}.{column} contains {label}; refusing to publish it"
                )


def export_demo_database(
    *, source: Path, destination: Path, replace: bool = False
) -> DemoExportResult:
    """Copy the five Gold tables into a new portable DuckDB file.

    The file is built beside the destination and only moved into place after
    the path check passes and both dashboard readers can read it. The source
    database is opened read-only. A JSON manifest with the checksum and row
    counts is written next to the database.
    """
    source = source.resolve()
    if not source.is_file():
        raise DemoExportError(f"Source database does not exist: {source}")
    if destination.exists() and not replace:
        raise DemoExportError(f"{destination} exists; pass replace=True to rebuild it")
    _check_source(source)
    # The data root is '<root>/query/<database>'; its folder names must not leak.
    data_root = source.parent.parent
    forbidden = [str(data_root)]
    if len(Path.home().parts) > 2:
        forbidden.append(str(Path.home()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    # DuckDB keeps a write-ahead log beside the file until it checkpoints.
    leftovers = (partial, partial.with_name(partial.name + ".wal"))
    for leftover in leftovers:
        if leftover.exists():
            raise DemoExportError(f"Leftover file from an earlier run: {leftover}")
    try:
        with duckdb.connect(str(partial)) as connection:
            _copy_tables(connection, source, str(data_root))
            _check_published_text(connection, forbidden)
            with duckdb.connect(str(source), read_only=True) as original:
                counts = {}
                for table in DEMO_TABLES:
                    copied = connection.execute(
                        f"SELECT count(*) FROM gold.{table}"
                    ).fetchone()
                    expected = original.execute(
                        f"SELECT count(*) FROM gold.{table}"
                    ).fetchone()
                    if copied != expected or copied is None:
                        raise DemoExportError(f"Row count changed for gold.{table}")
                    counts[table] = int(copied[0])
            connection.execute("CHECKPOINT")
        # Both readers must accept the file exactly as the hosted app will open it.
        load_dashboard_snapshot(database_path=partial)
        load_company_snapshot(database_path=partial)
        os.replace(partial, destination)
    except (duckdb.Error, DashboardReadError, OSError, DemoExportError) as exc:
        # A failed cleanup must not hide the error that stopped the export.
        for leftover in leftovers:
            with contextlib.suppress(OSError):
                leftover.unlink(missing_ok=True)
        if isinstance(exc, DemoExportError):
            raise
        raise DemoExportError(f"Demo export failed: {exc}") from exc
    content = destination.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    manifest = destination.with_suffix(".json")
    manifest.write_text(
        json.dumps(
            {
                "database": destination.name,
                "sha256": sha256,
                "size_bytes": len(content),
                "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "source_database": source.name,
                "row_counts": counts,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return DemoExportResult(destination, manifest, sha256, len(content), counts)


def main(arguments: Sequence[str] | None = None) -> int:
    """Export the demo database and print what was written."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="Built database")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("demo/sec_edgar_demo.duckdb"),
        help="Demo database to write (default: demo/sec_edgar_demo.duckdb)",
    )
    parser.add_argument(
        "--replace", action="store_true", help="Rebuild an existing demo database"
    )
    args = parser.parse_args(arguments)
    try:
        result = export_demo_database(
            source=args.database, destination=args.output, replace=args.replace
        )
    except DemoExportError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Demo database: {result.destination} ({result.size_bytes:,} bytes)")
    print(f"SHA-256: {result.sha256}")
    for table, count in result.row_counts.items():
        print(f"  gold.{table}: {count:,} rows")
    print(f"Manifest: {result.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
