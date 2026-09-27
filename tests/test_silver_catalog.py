import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import duckdb
import pytest
from test_silver_parquet import DIMENSIONS_SCHEMA, FACTS_SCHEMA, REJECTIONS_SCHEMA
from test_silver_publication import changed_source, prepared, publish, read

from sec_edgar_lakehouse import (
    FilingReference,
    RejectedOccurrence,
    SilverCatalogError,
    SilverDimension,
    refresh_silver_catalog,
)
from sec_edgar_lakehouse import silver_catalog as catalog
from sec_edgar_lakehouse import silver_publication as publication


def published(
    tmp_path: Path, root: Path, number: int = 1
) -> publication.SilverPublicationResult:
    value, manifest, _ = prepared(tmp_path / f"input-{number}")
    reference = FilingReference(str(number), f"0000000099-24-{number:06}")
    source_id = f"occurrence-{number}"
    value = replace(
        value,
        reference=reference,
        accepted_facts=tuple(
            replace(
                fact,
                cik=reference.cik,
                accession_number=reference.accession_number,
                source_occurrence_id=source_id,
            )
            for fact in value.accepted_facts
        ),
        dimensions=(
            SilverDimension(
                source_id, "urn:test", "Axis", "EXPLICIT", "Member", None, "SEGMENT"
            ),
        ),
    )
    record = read(manifest)
    record.update(cik=reference.cik, accession_number=reference.accession_number)
    manifest = (
        manifest.parents[3]
        / f"cik={reference.cik}"
        / f"accession={reference.accession_number}"
        / "manifests"
        / "run.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(record))
    return publish(value, manifest, root)


def snapshot(root: Path) -> dict[Path, tuple[bytes, int]]:
    return {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def refresh(root: Path, db: Path) -> catalog.SilverCatalogResult:
    return refresh_silver_catalog(silver_directory=root, database_path=db)


def test_relations_schemas_counts_and_idempotency(tmp_path: Path) -> None:
    root = tmp_path / "Silver's space"
    version = published(tmp_path, root)
    before = snapshot(root)
    db = tmp_path / "query space" / "catalog.duckdb"
    result = refresh(root, db)
    assert result.status == "COMPLETE"
    assert (result.active_filing_count, result.failure_count) == (1, 0)
    assert (
        result.facts_count,
        result.fact_dimensions_count,
        result.rejected_facts_count,
    ) == (1, 1, 0)
    with pytest.raises(FrozenInstanceError):
        result.status = "PARTIAL"  # type: ignore[misc]
    with duckdb.connect(str(db)) as connection:
        relations = connection.execute(
            "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema='silver'"
        ).fetchall()
        assert dict(relations) == {
            "active_filings": "BASE TABLE",
            "catalog_failures": "BASE TABLE",
            "facts": "VIEW",
            "fact_dimensions": "VIEW",
            "rejected_facts": "VIEW",
        }
        for name, schema in (
            ("facts", FACTS_SCHEMA),
            ("fact_dimensions", DIMENSIONS_SCHEMA),
            ("rejected_facts", REJECTIONS_SCHEMA),
        ):
            actual = connection.execute(f"DESCRIBE silver.{name}").fetchall()
            assert tuple((r[0], r[1]) for r in actual) == schema
        assert connection.execute(
            "SELECT version_path, activation_run_id, accepted_count FROM silver.active_filings"
        ).fetchall() == [(str(version.version_path), "first", 1)]
        assert connection.execute(
            "SELECT source_occurrence_id FROM silver.facts"
        ).fetchall() == [("occurrence-1",)]
    assert refresh(root, db) == result
    assert snapshot(root) == before


def test_multiple_filings_and_orphan_exclusion(tmp_path: Path) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root, 1)
    published(tmp_path, root, 2)
    orphan = published(tmp_path, root, 3)
    orphan.active_path.unlink()
    result = refresh(root, tmp_path / "catalog.duckdb")
    assert result.active_filing_count == 2
    assert result.facts_count == result.fact_dimensions_count == 2
    assert result.failure_count == 0


def test_historical_version_excluded(tmp_path: Path) -> None:
    value, manifest, root = prepared(tmp_path)
    first = publish(value, manifest, root)
    newer = changed_source(value, manifest)
    second = publish(newer, manifest, root, "second")
    result = refresh(root, tmp_path / "catalog.duckdb")
    assert result.facts_count == 1
    with duckdb.connect(str(result.database_path)) as connection:
        assert connection.execute(
            "SELECT source_sha256 FROM silver.facts"
        ).fetchall() == [(newer.selected_document_sha256,)]
        assert connection.execute(
            "SELECT version_path FROM silver.active_filings"
        ).fetchall() == [(str(second.version_path),)]
    assert first.version_path.exists()


def test_partial_and_no_valid_preserve_catalog(tmp_path: Path) -> None:
    root = tmp_path / "silver"
    good = published(tmp_path, root, 1)
    bad = published(tmp_path, root, 2)
    bad.active_path.write_text("broken json")
    db = tmp_path / "catalog.duckdb"
    result = refresh(root, db)
    assert result.status == "PARTIAL" and result.failure_count == 1
    with duckdb.connect(str(db)) as connection:
        failures = connection.execute(
            "SELECT * FROM silver.catalog_failures"
        ).fetchall()
        assert failures[0][:3] == (
            "0000000002",
            "0000000099-24-000002",
            bad.active_path.relative_to(root).as_posix(),
        )
        assert failures[0][3]
    good.active_path.unlink()
    with pytest.raises(SilverCatalogError, match="No verified"):
        refresh(root, db)
    with duckdb.connect(str(db)) as connection:
        assert connection.execute("SELECT count(*) FROM silver.facts").fetchone() == (
            1,
        )
        assert (
            connection.execute("SELECT * FROM silver.catalog_failures").fetchall()
            == failures
        )


@pytest.mark.parametrize("existing", [False, True])
def test_transaction_failure_rolls_back_and_cleans_only_new_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root, 1)
    db = tmp_path / "catalog.duckdb"
    if existing:
        refresh(root, db)
        with duckdb.connect(str(db)) as connection:
            connection.execute("CREATE TABLE main.notes AS SELECT 42 AS value")
    published(tmp_path, root, 2)
    original = catalog._verify_catalog

    def fail(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise RuntimeError("injected final verification failure")

    monkeypatch.setattr(catalog, "_verify_catalog", fail)
    with pytest.raises(SilverCatalogError, match="injected"):
        refresh(root, db)
    if existing:
        with duckdb.connect(str(db)) as connection:
            assert connection.execute("SELECT * FROM main.notes").fetchall() == [(42,)]
            assert connection.execute(
                "SELECT count(*) FROM silver.active_filings"
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT source_occurrence_id FROM silver.facts"
            ).fetchall() == [("occurrence-1",)]
            assert connection.execute(
                "SELECT count(*) FROM silver.fact_dimensions"
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT count(*) FROM silver.catalog_failures"
            ).fetchone() == (0,)
    else:
        assert not db.exists()
        assert not db.with_name(db.name + ".wal").exists()


@pytest.mark.parametrize("constant", ["PARSER_VERSION", "SCHEMA_VERSION"])
def test_unsupported_versions_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constant: str
) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root, 1)
    with monkeypatch.context() as patch:
        patch.setattr(publication.parquet, constant, "2")
        published(tmp_path, root, 2)
    result = refresh(root, tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL" and result.active_filing_count == 1
    with duckdb.connect(str(result.database_path)) as connection:
        assert (
            "Unsupported"
            in connection.execute(
                "SELECT reason FROM silver.catalog_failures"
            ).fetchall()[0][0]
        )


def reseal(version: publication.SilverPublicationResult) -> None:
    record = read(version.publication_path)
    record["files"] = publication._file_evidence(version.version_path)
    version.publication_path.write_text(json.dumps(record))
    pointer = read(version.active_path)
    pointer["publication_sha256"] = hashlib.sha256(
        version.publication_path.read_bytes()
    ).hexdigest()
    version.active_path.write_text(json.dumps(pointer))


@pytest.mark.parametrize(
    "mutation",
    [
        "checksum",
        "schema",
        "count",
        "links",
        "traversal",
        "identity",
        "publication_hash",
    ],
)
def test_corrupt_active_filing_excluded(tmp_path: Path, mutation: str) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root, 1)
    bad = published(tmp_path, root, 2)
    if mutation == "checksum":
        (bad.version_path / "facts.parquet").write_bytes(b"bad")
    elif mutation in {"schema", "count", "links"}:
        name = "fact_dimensions" if mutation == "links" else "facts"
        path = bad.version_path / f"{name}.parquet"
        with duckdb.connect(":memory:") as connection:
            connection.execute(
                "CREATE TABLE rows AS SELECT * FROM read_parquet(?, hive_partitioning=false)",
                [str(path)],
            )
            if mutation == "schema":
                connection.execute("ALTER TABLE rows DROP COLUMN raw_value")
            elif mutation == "count":
                connection.execute("DELETE FROM rows")
            else:
                connection.execute("UPDATE rows SET source_occurrence_id='missing'")
            connection.execute("COPY rows TO ? (FORMAT PARQUET)", [str(path)])
        reseal(bad)
    else:
        pointer = read(bad.active_path)
        if mutation == "traversal":
            pointer["version_path"] = "../../outside"
        elif mutation == "identity":
            pointer["cik"] = "0000000001"
        else:
            pointer["publication_sha256"] = "0" * 64
        bad.active_path.write_text(json.dumps(pointer))
    before = snapshot(root)
    result = refresh(root, tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL" and result.failure_count == 1
    assert result.facts_count == 1
    assert snapshot(root) == before


@pytest.mark.parametrize("component", ["active", "version", "filing", "company"])
def test_managed_symlinks_do_not_follow_outside_paths(
    tmp_path: Path, component: str
) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root, 1)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "untouched"
    marker.write_text("keep")
    if component == "company":
        target = root / "cik=0000000002"
    else:
        bad = published(tmp_path, root, 2)
        if component == "active":
            target = bad.active_path
            target.unlink()
        elif component == "version":
            target = bad.version_path
            target.rename(target.with_name("old-version"))
        else:
            target = bad.active_path.parent
            target.rename(target.with_name("old-filing"))
    try:
        target.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(str(exc))
    before = snapshot(outside)
    result = refresh(root, tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL" and result.facts_count == 1
    assert snapshot(outside) == before


def test_system_alias_and_invalid_directory_evidence(tmp_path: Path) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root, 1)
    bad = root / "cik=bad" / "accession=bad"
    bad.mkdir(parents=True)
    (bad / "active.json").write_text("{}")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(str(exc))
    result = refresh(alias / "silver", tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL"
    with duckdb.connect(str(result.database_path)) as connection:
        assert connection.execute(
            "SELECT cik, accession_number FROM silver.catalog_failures"
        ).fetchall() == [(None, None)]


def test_empty_root_and_database_inside_silver_refused(tmp_path: Path) -> None:
    root = tmp_path / "silver"
    root.mkdir()
    db = tmp_path / "catalog.duckdb"
    with pytest.raises(SilverCatalogError, match="no active pointers"):
        refresh(root, db)
    assert not db.exists()
    published(tmp_path, root)
    with pytest.raises(SilverCatalogError, match="outside"):
        refresh(root, root / "catalog.duckdb")
    assert not (root / "catalog.duckdb").exists()


def test_duplicate_ids_across_filings_fail_final_verification(tmp_path: Path) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root, 1)
    db = tmp_path / "catalog.duckdb"
    refresh(root, db)
    second = published(tmp_path, root, 2)
    for name in ("facts", "fact_dimensions"):
        path = second.version_path / f"{name}.parquet"
        with duckdb.connect(":memory:") as connection:
            connection.execute(
                "CREATE TABLE rows AS SELECT * FROM read_parquet(?, hive_partitioning=false)",
                [str(path)],
            )
            connection.execute("UPDATE rows SET source_occurrence_id='occurrence-1'")
            connection.execute("COPY rows TO ? (FORMAT PARQUET)", [str(path)])
    reseal(second)
    with pytest.raises(SilverCatalogError, match="globally unique"):
        refresh(root, db)
    with duckdb.connect(str(db)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM silver.active_filings"
        ).fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM silver.facts").fetchone() == (
            1,
        )


def test_partial_silver_is_valid_catalog_input_with_rejections(tmp_path: Path) -> None:
    value, manifest, root = prepared(tmp_path)
    rejected = RejectedOccurrence(
        "rejected",
        2,
        "urn:test",
        "BadFact",
        "bad",
        None,
        None,
        None,
        None,
        (("contextRef", "missing"),),
        "INVALID_CONTEXT",
        "Missing context",
    )
    value = replace(
        value,
        status="PARTIAL",
        rejected_occurrences=(rejected,),
        rejected_count=1,
        candidate_count=2,
    )
    publish(value, manifest, root)
    result = refresh(root, tmp_path / "catalog.duckdb")
    assert result.status == "COMPLETE" and result.rejected_facts_count == 1
    assert result.fact_dimensions_count == 0
    with duckdb.connect(str(result.database_path)) as connection:
        assert connection.execute(
            "SELECT silver_status FROM silver.active_filings"
        ).fetchall() == [("PARTIAL",)]
        assert connection.execute(
            "SELECT reason_code FROM silver.rejected_facts"
        ).fetchall() == [("INVALID_CONTEXT",)]
        columns = connection.execute("DESCRIBE silver.active_filings").fetchall()
        assert [(r[0], r[1]) for r in columns] == [
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
        ]
        assert all(row[2] == "NO" for row in columns)
        failure_columns = connection.execute(
            "DESCRIBE silver.catalog_failures"
        ).fetchall()
        assert [(r[0], r[1], r[2]) for r in failure_columns] == [
            ("cik", "VARCHAR", "YES"),
            ("accession_number", "VARCHAR", "YES"),
            ("active_pointer_path", "VARCHAR", "NO"),
            ("reason", "VARCHAR", "NO"),
        ]


def test_preexisting_invalid_database_and_wal_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root)
    db = tmp_path / "catalog.duckdb"
    db.write_bytes(b"not a database")
    with pytest.raises(SilverCatalogError):
        refresh(root, db)
    assert db.read_bytes() == b"not a database"
    other = tmp_path / "other.duckdb"
    wal = tmp_path / "other.duckdb.wal"
    wal.write_bytes(b"existing evidence")
    with pytest.raises(SilverCatalogError, match="existing WAL"):
        refresh(root, other)
    assert wal.read_bytes() == b"existing evidence"
    assert not other.exists()
