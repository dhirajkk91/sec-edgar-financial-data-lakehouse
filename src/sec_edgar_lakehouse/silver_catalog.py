"""Build a local SQL snapshot over verified active Silver publications."""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import duckdb

from sec_edgar_lakehouse import silver_parquet as parquet
from sec_edgar_lakehouse import silver_publication as publication

_ACTIVE_SCHEMA = (
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("source_document_name", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
    ("silver_status", "VARCHAR"),
    ("parser_version", "VARCHAR"),
    ("schema_version", "VARCHAR"),
    ("activation_run_id", "VARCHAR"),
    ("activated_at", "TIMESTAMP WITH TIME ZONE"),
    ("version_path", "VARCHAR"),
    ("publication_path", "VARCHAR"),
    ("publication_sha256", "VARCHAR"),
    ("accepted_count", "BIGINT"),
    ("dimension_count", "BIGINT"),
    ("rejected_count", "BIGINT"),
    ("candidate_count", "BIGINT"),
)
_FAILURE_SCHEMA = (
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("active_pointer_path", "VARCHAR"),
    ("reason", "VARCHAR"),
)
_VIEWS = {
    "facts": (parquet._FACTS_SCHEMA, "accepted_count"),
    "fact_dimensions": (parquet._DIMENSIONS_SCHEMA, "dimension_count"),
    "rejected_facts": (parquet._REJECTIONS_SCHEMA, "rejected_count"),
}


class SilverCatalogError(Exception):
    """The catalog could not be refreshed; no new snapshot was committed."""


@dataclass(frozen=True, slots=True)
class SilverCatalogResult:
    """The verified active snapshot now exposed by the local database."""

    status: Literal["COMPLETE", "PARTIAL"]
    database_path: Path
    active_filing_count: int
    failure_count: int
    facts_count: int
    fact_dimensions_count: int
    rejected_facts_count: int


def refresh_silver_catalog(
    *, silver_directory: Path, database_path: Path
) -> SilverCatalogResult:
    """Refresh external views over all verified active filings under a Silver root."""
    try:
        if not isinstance(silver_directory, Path) or not isinstance(
            database_path, Path
        ):
            raise SilverCatalogError(
                "Silver directory and database must be pathlib.Path values"
            )
        root = silver_directory.resolve(strict=True)
        if not root.is_dir():
            raise SilverCatalogError(f"Silver root must be a directory: {root}")
        database = database_path.resolve()
        if database.is_relative_to(root):
            raise SilverCatalogError(
                "Database must be outside the Silver source directory"
            )
        if database.exists() and not database.is_file():
            raise SilverCatalogError(f"Database path must be a file: {database}")
        active, failures = _discover(root)
        if not active:
            detail = "; ".join(str(row[3]) for row in failures)
            raise SilverCatalogError(
                "No verified active Silver filings remain"
                + (f": {detail}" if detail else " (no active pointers)")
            )
        counts = {
            name: sum(item.publication[key] for item in active)
            for name, (_, key) in _VIEWS.items()
        }
        _refresh_database(database, active, failures, counts)
        return SilverCatalogResult(
            "PARTIAL" if failures else "COMPLETE",
            database,
            len(active),
            len(failures),
            counts["facts"],
            counts["fact_dimensions"],
            counts["rejected_facts"],
        )
    except SilverCatalogError:
        raise
    except Exception as exc:
        raise SilverCatalogError(f"Catalog refresh failed: {exc}") from exc


def _failure(path: Path, root: Path, reason: str) -> tuple[Any, ...]:
    cik = path.parent.parent.name.removeprefix("cik=")
    accession = path.parent.name.removeprefix("accession=")
    valid_cik = re.fullmatch(r"[0-9]{10}", cik) is not None and int(cik) > 0
    valid_accession = (
        re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession) is not None
    )
    return (
        cik if valid_cik else None,
        accession if valid_accession else None,
        path.relative_to(root).as_posix(),
        reason,
    )


def _discover(
    root: Path,
) -> tuple[list[publication._ActiveVersion], list[tuple[Any, ...]]]:
    active = []
    failures = []
    # Never descend into versions: only an active pointer can select financial rows.
    for company in sorted(root.iterdir()):
        if company.is_symlink():
            failures.append(
                _failure(
                    company / "active.json",
                    root,
                    "Managed company symlink is forbidden",
                )
            )
            continue
        if not company.is_dir():
            continue
        for filing in sorted(company.iterdir()):
            path = filing / "active.json"
            if filing.is_symlink():
                failures.append(
                    _failure(path, root, "Managed filing symlink is forbidden")
                )
                continue
            if not filing.is_dir() or not (path.exists() or path.is_symlink()):
                continue
            try:
                item = publication._load_active(filing, root=root)
                if item is None:
                    raise SilverCatalogError(
                        "Active pointer disappeared during refresh"
                    )
                if (
                    item.publication["parser_version"] != parquet.PARSER_VERSION
                    or item.publication["schema_version"] != parquet.SCHEMA_VERSION
                ):
                    raise SilverCatalogError("Unsupported parser or schema version")
                active.append(item)
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                duckdb.Error,
                publication.SilverPublicationError,
                parquet.SilverWriteError,
                SilverCatalogError,
            ) as exc:
                failures.append(_failure(path, root, str(exc)))
    return active, failures


def _active_row(item: publication._ActiveVersion) -> tuple[Any, ...]:
    values = {
        **item.publication,
        "activation_run_id": item.pointer["processing_run_id"],
        "activated_at": datetime.fromisoformat(item.pointer["activated_at"]),
        "version_path": str(item.version_path),
        "publication_path": str(item.publication_path),
        "publication_sha256": item.pointer["publication_sha256"],
    }
    return tuple(values[name] for name, _ in _ACTIVE_SCHEMA)


def _literal(value: str) -> str:
    # DuckDB view definitions cannot bind parameters; quote paths as SQL literals.
    return "'" + value.replace("'", "''") + "'"


def _refresh_database(
    database: Path,
    active: list[publication._ActiveVersion],
    failures: list[tuple[Any, ...]],
    counts: dict[str, int],
) -> None:
    existed = database.exists()
    wal = database.with_name(database.name + ".wal")
    if not existed and (wal.exists() or wal.is_symlink()):
        raise SilverCatalogError(
            f"Refusing database creation beside an existing WAL: {wal}"
        )
    database.parent.mkdir(parents=True, exist_ok=True)
    try:
        with duckdb.connect(str(database)) as connection:
            # Metadata and paths must move together, including when verification fails.
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute("CREATE SCHEMA IF NOT EXISTS silver")
                for name, schema, rows in (
                    (
                        "active_filings",
                        _ACTIVE_SCHEMA,
                        [_active_row(item) for item in active],
                    ),
                    ("catalog_failures", _FAILURE_SCHEMA, failures),
                ):
                    columns = ", ".join(
                        f'"{column}" {kind}'
                        + (
                            ""
                            if name == "catalog_failures"
                            and column in {"cik", "accession_number"}
                            else " NOT NULL"
                        )
                        for column, kind in schema
                    )
                    constraint = (
                        ", PRIMARY KEY (cik, accession_number)"
                        if name == "active_filings"
                        else ""
                    )
                    connection.execute(
                        f"CREATE OR REPLACE TABLE silver.{name} ({columns}{constraint})"
                    )
                    if rows:
                        parameters = ", ".join("?" for _ in schema)
                        connection.executemany(
                            f"INSERT INTO silver.{name} VALUES ({parameters})", rows
                        )
                for name in _VIEWS:
                    paths = ", ".join(
                        _literal(str(item.version_path / f"{name}.parquet"))
                        for item in active
                    )
                    # Partition-shaped directories must not add columns to Silver's schema.
                    connection.execute(
                        f"CREATE OR REPLACE VIEW silver.{name} AS "
                        f"SELECT * FROM read_parquet([{paths}], hive_partitioning=false)"
                    )
                _verify_catalog(connection, len(active), len(failures), counts)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
    except Exception as exc:
        if not existed:
            for artifact in (wal, database):
                artifact.unlink(missing_ok=True)
        raise SilverCatalogError(f"Catalog transaction failed: {exc}") from exc


def _verify_catalog(
    connection: duckdb.DuckDBPyConnection,
    active_count: int,
    failure_count: int,
    counts: dict[str, int],
) -> None:
    schemas = {
        "active_filings": _ACTIVE_SCHEMA,
        "catalog_failures": _FAILURE_SCHEMA,
        **{name: schema for name, (schema, _) in _VIEWS.items()},
    }
    expected_counts = {
        "active_filings": active_count,
        "catalog_failures": failure_count,
        **counts,
    }
    for name, expected in schemas.items():
        actual = connection.execute(f"DESCRIBE silver.{name}").fetchall()
        if tuple((row[0], row[1]) for row in actual) != expected:
            raise SilverCatalogError(f"Catalog schema mismatch: silver.{name}")
        count = connection.execute(f"SELECT count(*) FROM silver.{name}").fetchall()[0][
            0
        ]
        if count != expected_counts[name]:
            raise SilverCatalogError(f"Catalog row count mismatch: silver.{name}")
    duplicates = connection.execute(
        "SELECT count(*) - count(DISTINCT source_occurrence_id) FROM silver.facts"
    ).fetchall()[0][0]
    if duplicates:
        raise SilverCatalogError("Fact occurrence IDs are not globally unique")
    missing = connection.execute(
        "SELECT count(*) FROM silver.fact_dimensions d WHERE NOT EXISTS "
        "(SELECT 1 FROM silver.facts f WHERE f.source_occurrence_id = d.source_occurrence_id)"
    ).fetchall()[0][0]
    if missing:
        raise SilverCatalogError("Catalog dimensions refer to missing facts")
