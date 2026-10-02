import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import duckdb
import pytest
from test_company_filings import payload
from test_silver_catalog import published

from sec_edgar_lakehouse import (
    FilingMetadataError,
    load_filing_metadata,
    refresh_silver_catalog,
)
from sec_edgar_lakehouse import filing_metadata as metadata

CIK = "0001122304"
ACCESSIONS = tuple(f"0001193125-25-{n:06}" for n in (1, 2, 3, 4))


def evidence(
    tmp_path: Path, data: dict[str, Any] | None = None, run_id: str = "run-one"
) -> Path:
    directory = tmp_path / "runs" / f"cik={CIK}" / f"run_id={run_id}"
    directory.mkdir(parents=True)
    content = json.dumps(payload() if data is None else data, indent=2).encode() + b"\n"
    (directory / "submissions.json").write_bytes(content)
    record = {
        "schema_version": "1",
        "cik": CIK,
        "run_id": run_id,
        "status": "COMPLETE",
        "selection": {"limit": 2},
        "submissions_evidence": {
            "path": "submissions.json",
            "url": f"https://data.sec.gov/submissions/CIK{CIK}.json",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    }
    path = directory / "run.json"
    path.write_text(json.dumps(record))
    return path


def catalog(tmp_path: Path, accessions: tuple[str, ...] = ACCESSIONS[:2]) -> Path:
    database = tmp_path / "catalog.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA silver")
        connection.execute(
            "CREATE TABLE silver.active_filings (cik VARCHAR, accession_number VARCHAR, PRIMARY KEY(cik, accession_number))"
        )
        for accession in accessions:
            connection.execute(
                "INSERT INTO silver.active_filings VALUES (?, ?)", [CIK, accession]
            )
    return database


def rows(database: Path) -> list[tuple[Any, ...]]:
    # SQL-to-text avoids DuckDB's optional pytz dependency during Python retrieval.
    columns = ", ".join(
        f'cast("{name}" as VARCHAR)'
        if name == "submissions_retrieved_at"
        else f'"{name}"'
        for name, _, _ in metadata._METADATA_SCHEMA
    )
    with duckdb.connect(str(database), read_only=True) as connection:
        values = connection.execute(
            f"SELECT {columns} FROM silver.filing_metadata ORDER BY cik, accession_number"
        ).fetchall()
    return [(*row[:10], datetime.fromisoformat(row[10]), *row[11:]) for row in values]


def rewrite(path: Path, update: Any) -> None:
    record = json.loads(path.read_text())
    update(record)
    path.write_text(json.dumps(record))


def assert_counts(result: metadata.FilingMetadataLoadResult) -> None:
    assert (
        result.active_filing_count
        == result.inserted_count
        + result.already_existing_count
        + result.missing_count
        + result.conflict_count
    )


@pytest.mark.parametrize("status", ["COMPLETE", "PARTIAL", "FAILED"])
def test_verified_load_all_snapshot_rows_and_rerun(tmp_path: Path, status: str) -> None:
    path = evidence(tmp_path)
    rewrite(path, lambda record: record.update(status=status))
    database = catalog(tmp_path)
    result = load_filing_metadata(run_record_path=path, database_path=database)
    assert result.status == "COMPLETE"
    assert (
        result.database_path == database.resolve()
        and result.cik == CIK
        and result.source_run_id == "run-one"
    )
    assert (
        result.active_filing_count,
        result.inserted_count,
        result.already_existing_count,
        result.missing_count,
        result.conflict_count,
    ) == (2, 2, 0, 0, 0)
    assert result.issues == ()
    assert_counts(result)
    stored = rows(database)
    assert len(stored) == 2
    assert stored[0] == (
        CIK,
        ACCESSIONS[0],
        "10-Q",
        date(2025, 1, 1),
        None,
        "one.htm",
        "run-one",
        str(path.resolve()),
        str(path.with_name("submissions.json").resolve()),
        f"https://data.sec.gov/submissions/CIK{CIK}.json",
        datetime(2026, 1, 1, tzinfo=UTC),
        path.with_name("submissions.json").stat().st_size,
        hashlib.sha256(path.with_name("submissions.json").read_bytes()).hexdigest(),
    )
    rerun = load_filing_metadata(run_record_path=path, database_path=database)
    assert (rerun.inserted_count, rerun.already_existing_count) == (0, 2)
    assert rows(database) == stored
    assert_counts(rerun)
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.status = "PARTIAL"  # type: ignore[misc]
    with duckdb.connect(str(database), read_only=True) as connection:
        columns = tuple(
            row[:3]
            for row in connection.execute("DESCRIBE silver.filing_metadata").fetchall()
        )
        assert columns == metadata._METADATA_SCHEMA
        keys = connection.execute(
            "SELECT constraint_column_names FROM duckdb_constraints() WHERE table_name='filing_metadata' AND constraint_type='PRIMARY KEY'"
        ).fetchall()
        assert keys == [(["cik", "accession_number"],)]


def test_identical_new_snapshot_preserves_first_provenance(tmp_path: Path) -> None:
    database = catalog(tmp_path)
    first = evidence(tmp_path)
    load_filing_metadata(run_record_path=first, database_path=database)
    stored = rows(database)
    second = evidence(tmp_path, run_id="run-two")
    rewrite(
        second,
        lambda record: record["submissions_evidence"].update(
            retrieved_at="2026-02-01T00:00:00Z"
        ),
    )
    result = load_filing_metadata(run_record_path=second, database_path=database)
    assert result.source_run_id == "run-two" and result.already_existing_count == 2
    assert rows(database) == stored


def test_missing_conflicts_valid_rows_and_ordering(tmp_path: Path) -> None:
    database = catalog(tmp_path, (ACCESSIONS[1],))
    first = evidence(tmp_path)
    load_filing_metadata(run_record_path=first, database_path=database)
    stored = rows(database)
    with duckdb.connect(str(database)) as connection:
        for accession in (
            ACCESSIONS[3],
            ACCESSIONS[0],
            ACCESSIONS[2],
            "0001193125-25-000005",
        ):
            connection.execute(
                "INSERT INTO silver.active_filings VALUES (?, ?)", [CIK, accession]
            )
    data = payload()
    recent = data["filings"]["recent"]
    for column in recent.values():
        column.pop(0)
    recent["form"][0] = "10-K/A"
    recent["filingDate"][0] = "2025-03-02"
    recent["reportDate"][0] = ""
    recent["primaryDocument"][0] = "different.htm"
    second = evidence(tmp_path, data, "run-two")
    result = load_filing_metadata(run_record_path=second, database_path=database)
    assert result.status == "PARTIAL"
    assert (
        result.active_filing_count,
        result.inserted_count,
        result.already_existing_count,
        result.missing_count,
        result.conflict_count,
    ) == (5, 2, 0, 2, 1)
    assert_counts(result)
    assert [item.accession_number for item in result.issues] == [
        ACCESSIONS[0],
        ACCESSIONS[1],
        "0001193125-25-000005",
    ]
    assert [item.reason_code for item in result.issues] == [
        "MISSING_METADATA",
        "METADATA_CONFLICT",
        "MISSING_METADATA",
    ]
    assert (
        result.issues[1].message
        == "Conflicting fields: form, filing_date, report_date, primary_document"
    )
    assert rows(database)[0] == stored[0]
    assert rows(database)[-1][5] == "xslF345X06/form4.xml"
    issue = result.issues[0]
    assert not hasattr(issue, "__dict__")
    with pytest.raises(FrozenInstanceError):
        issue.message = "changed"  # type: ignore[misc]


def test_inactive_and_other_company_metadata_remain(tmp_path: Path) -> None:
    database = catalog(tmp_path, ACCESSIONS)
    path = evidence(tmp_path)
    load_filing_metadata(run_record_path=path, database_path=database)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "DELETE FROM silver.active_filings WHERE accession_number=?",
            [ACCESSIONS[3]],
        )
        connection.execute(
            "UPDATE silver.filing_metadata SET cik='0000000001' WHERE accession_number=?",
            [ACCESSIONS[2]],
        )
        connection.execute(
            "DELETE FROM silver.active_filings WHERE accession_number=?",
            [ACCESSIONS[2]],
        )
    before = rows(database)
    result = load_filing_metadata(run_record_path=path, database_path=database)
    assert result.active_filing_count == result.already_existing_count == 2
    assert rows(database) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "bytes",
        "size",
        "hash",
        "missing_file",
        "missing_evidence",
        "invalid_json",
        "duplicate_key",
        "constant",
        "schema",
        "cik",
        "run_id",
        "run_directory",
        "cik_directory",
        "unsafe_id",
        "evidence_path",
        "url",
        "naive_time",
        "offset_time",
        "bad_time",
        "zero_size",
        "bool_size",
        "float_size",
        "upper_hash",
        "short_hash",
        "wrong_submissions_cik",
        "bad_date",
        "bad_document",
        "bad_arrays",
        "bad_submissions_json",
    ],
)
def test_invalid_evidence_precedes_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    path = evidence(tmp_path)
    source = path.with_name("submissions.json")
    updates = {
        "schema": lambda r: r.update(schema_version="2"),
        "cik": lambda r: r.update(cik="1122304"),
        "run_id": lambda r: r.update(run_id="other"),
        "unsafe_id": lambda r: r.update(run_id="../escape"),
        "missing_evidence": lambda r: r.update(submissions_evidence=None),
        "size": lambda r: r["submissions_evidence"].update(size_bytes=1),
        "hash": lambda r: r["submissions_evidence"].update(sha256="0" * 64),
        "evidence_path": lambda r: r["submissions_evidence"].update(
            path="../submissions.json"
        ),
        "url": lambda r: r["submissions_evidence"].update(
            url="https://example.org/submissions"
        ),
        "naive_time": lambda r: r["submissions_evidence"].update(
            retrieved_at="2026-01-01T00:00:00"
        ),
        "offset_time": lambda r: r["submissions_evidence"].update(
            retrieved_at="2026-01-01T00:00:00+01:00"
        ),
        "bad_time": lambda r: r["submissions_evidence"].update(retrieved_at="invalid"),
        "zero_size": lambda r: r["submissions_evidence"].update(size_bytes=0),
        "bool_size": lambda r: r["submissions_evidence"].update(size_bytes=True),
        "float_size": lambda r: r["submissions_evidence"].update(size_bytes=1.5),
        "upper_hash": lambda r: r["submissions_evidence"].update(sha256="A" * 64),
        "short_hash": lambda r: r["submissions_evidence"].update(sha256="a" * 63),
    }
    if mutation in updates:
        rewrite(path, updates[mutation])
    elif mutation == "bytes":
        source.write_bytes(b"corrupt")
    elif mutation == "missing_file":
        source.unlink()
    elif mutation in ("invalid_json", "duplicate_key", "constant"):
        path.write_text(
            {
                "invalid_json": "{",
                "duplicate_key": '{"schema_version":"1","schema_version":"1"}',
                "constant": '{"schema_version":NaN}',
            }[mutation]
        )
    elif mutation.endswith("directory"):
        directory = path.parent if mutation == "run_directory" else path.parent.parent
        destination = directory.with_name("wrong")
        directory.rename(destination)
        path = (
            destination / "run.json"
            if mutation == "run_directory"
            else destination / path.parent.name / "run.json"
        )
    else:
        data = payload()
        if mutation == "wrong_submissions_cik":
            data["cik"] = 1
        elif mutation == "bad_date":
            data["filings"]["recent"]["filingDate"][0] = "2025-02-30"
        elif mutation == "bad_document":
            data["filings"]["recent"]["primaryDocument"][0] = "%2e%2e/escape"
        elif mutation == "bad_arrays":
            data["filings"]["recent"]["form"].pop()
        content = (
            b"{" if mutation == "bad_submissions_json" else json.dumps(data).encode()
        )
        source.write_bytes(content)
        rewrite(
            path,
            lambda r: r["submissions_evidence"].update(
                size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest()
            ),
        )
    connect = Mock()
    monkeypatch.setattr(metadata.duckdb, "connect", connect)
    with pytest.raises(FilingMetadataError):
        load_filing_metadata(
            run_record_path=path, database_path=tmp_path / "does-not-exist.duckdb"
        )
    connect.assert_not_called()
    assert not (tmp_path / "does-not-exist.duckdb").exists()


@pytest.mark.parametrize(
    "component", ["root", "company", "run", "record", "submissions"]
)
def test_managed_symlinks_rejected(tmp_path: Path, component: str) -> None:
    path = evidence(tmp_path)
    target = {
        "root": path.parents[2],
        "company": path.parents[1],
        "run": path.parent,
        "record": path,
        "submissions": path.with_name("submissions.json"),
    }[component]
    moved = target.with_name(target.name + "-original")
    target.rename(moved)
    try:
        target.symlink_to(moved, target_is_directory=moved.is_dir())
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(FilingMetadataError, match="symlink|directory"):
        load_filing_metadata(
            run_record_path=path, database_path=tmp_path / "missing.duckdb"
        )


def test_system_alias_and_bound_values(tmp_path: Path) -> None:
    data = payload()
    data["filings"]["recent"]["form"][0] = "10-Q'; DROP TABLE silver.active_filings; --"
    path = evidence(tmp_path, data)
    database = catalog(tmp_path)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    result = load_filing_metadata(
        run_record_path=alias / path.relative_to(tmp_path),
        database_path=alias / database.name,
    )
    assert result.database_path == database.resolve()
    assert rows(database)[0][7] == str(path.resolve())
    assert rows(database)[0][2] == data["filings"]["recent"]["form"][0]


@pytest.mark.parametrize(
    "kind", ["missing", "directory", "invalid_file", "no_active", "wrong_type"]
)
def test_invalid_database(tmp_path: Path, kind: str) -> None:
    path = evidence(tmp_path)
    database: Any = tmp_path / "database.duckdb"
    if kind == "directory":
        database.mkdir()
    elif kind == "invalid_file":
        database.write_bytes(b"not a database")
    elif kind == "no_active":
        with duckdb.connect(str(database)) as connection:
            connection.execute("CREATE TABLE notes (value INTEGER)")
    elif kind == "wrong_type":
        database = str(database)
    with pytest.raises(FilingMetadataError):
        load_filing_metadata(run_record_path=path, database_path=database)
    if kind == "missing":
        assert not database.exists()
    if kind == "invalid_file":
        assert database.read_bytes() == b"not a database"


@pytest.mark.parametrize("existing", [False, True])
def test_insert_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    path = evidence(tmp_path)
    database = catalog(tmp_path)
    before = []
    if existing:
        load_filing_metadata(run_record_path=path, database_path=database)
        with duckdb.connect(str(database)) as connection:
            connection.execute("DELETE FROM silver.filing_metadata")
        before = rows(database)
    insert = metadata._insert_metadata
    count = 0

    def fail(connection: duckdb.DuckDBPyConnection, row: tuple[Any, ...]) -> None:
        nonlocal count
        count += 1
        if count == 2:
            raise duckdb.Error("injected insert failure")
        insert(connection, row)

    monkeypatch.setattr(metadata, "_insert_metadata", fail)
    with pytest.raises(FilingMetadataError, match="injected"):
        load_filing_metadata(run_record_path=path, database_path=database)
    if existing:
        assert rows(database) == before
    else:
        with duckdb.connect(str(database)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='silver' AND table_name='filing_metadata'"
            ).fetchone() == (0,)


@pytest.mark.parametrize("kind", ["view", "column_type", "nullable", "order", "no_key"])
def test_incompatible_existing_relation(tmp_path: Path, kind: str) -> None:
    path = evidence(tmp_path)
    database = catalog(tmp_path)
    with duckdb.connect(str(database)) as connection:
        if kind == "view":
            connection.execute(
                "CREATE VIEW silver.filing_metadata AS SELECT 1 AS value"
            )
        else:
            schema = list(metadata._METADATA_SCHEMA)
            if kind == "column_type":
                schema[3] = ("filing_date", "VARCHAR", "NO")
            elif kind == "nullable":
                schema[2] = ("form", "VARCHAR", "YES")
            elif kind == "order":
                schema[2], schema[3] = schema[3], schema[2]
            columns = ", ".join(
                f'"{name}" {dtype}' + (" NOT NULL" if nullable == "NO" else "")
                for name, dtype, nullable in schema
            )
            key = "" if kind == "no_key" else ", PRIMARY KEY(cik, accession_number)"
            connection.execute(f"CREATE TABLE silver.filing_metadata ({columns}{key})")
    with pytest.raises(FilingMetadataError, match="table|schema"):
        load_filing_metadata(run_record_path=path, database_path=database)


def test_zero_active_filings(tmp_path: Path) -> None:
    path = evidence(tmp_path)
    result = load_filing_metadata(
        run_record_path=path, database_path=catalog(tmp_path, ())
    )
    assert (
        result.status == "COMPLETE"
        and result.active_filing_count == result.inserted_count == 0
    )
    assert result.issues == ()
    assert_counts(result)


def test_refresh_preserves_complete_metadata_table(tmp_path: Path) -> None:
    root = tmp_path / "silver"
    published(tmp_path, root)
    database = tmp_path / "catalog.duckdb"
    refresh_silver_catalog(silver_directory=root, database_path=database)
    data = payload()
    data["cik"] = 1
    data["filings"]["recent"]["accessionNumber"][0] = "0000000099-24-000001"
    path = evidence(tmp_path, data)
    new_parent = path.parent.parent.with_name("cik=0000000001")
    path.parent.parent.rename(new_parent)
    path = new_parent / path.parent.name / "run.json"
    rewrite(
        path,
        lambda r: (
            r.update(cik="0000000001"),
            r["submissions_evidence"].update(
                url="https://data.sec.gov/submissions/CIK0000000001.json"
            ),
        ),
    )
    load_filing_metadata(run_record_path=path, database_path=database)
    before = rows(database)
    refresh_silver_catalog(silver_directory=root, database_path=database)
    assert rows(database) == before and len(before) == 1


def test_programming_errors_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = evidence(tmp_path)
    database = catalog(tmp_path)
    monkeypatch.setattr(
        metadata, "_insert_metadata", Mock(side_effect=RuntimeError("bug"))
    )
    with pytest.raises(RuntimeError, match="bug"):
        load_filing_metadata(run_record_path=path, database_path=database)
