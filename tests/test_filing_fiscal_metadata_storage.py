import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import duckdb
import pytest
from test_filing_fiscal_metadata import CONCEPTS, document, fact, make_fixture
from test_silver_extraction import ACCESSION, DOCUMENT_NAME

from sec_edgar_lakehouse import FiscalMetadataLoadError, load_filing_fiscal_metadata
from sec_edgar_lakehouse import filing_fiscal_metadata_storage as storage


def load(paths: tuple[Path, Path, Path]):
    return load_filing_fiscal_metadata(paths[0], paths[1], database_path=paths[2])


def rows(database: Path) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(database), read_only=True) as c:
        return c.execute(
            "SELECT * FROM silver.filing_fiscal_metadata ORDER BY ALL"
        ).fetchall()


def test_insert_schema_rerun_json_and_single_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = make_fixture(tmp_path, document(year=" 2024\n"))
    extractor = Mock(wraps=storage.extract_filing_fiscal_metadata)
    monkeypatch.setattr(storage, "extract_filing_fiscal_metadata", extractor)
    result = load(paths)
    assert extractor.call_count == 1 and result.outcome == "INSERTED"
    assert result.status == "COMPLETE" and result.database_path == paths[2].resolve()
    assert result.database_path.is_absolute() and not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.outcome = "ALREADY_EXISTS"
    with duckdb.connect(str(paths[2]), read_only=True) as c:
        assert tuple(
            r[:3]
            for r in c.execute("DESCRIBE silver.filing_fiscal_metadata").fetchall()
        ) == (
            ("cik", "VARCHAR", "NO"),
            ("accession_number", "VARCHAR", "NO"),
            ("source_document_name", "VARCHAR", "NO"),
            ("source_sha256", "VARCHAR", "NO"),
            ("extraction_version", "VARCHAR", "NO"),
            ("form", "VARCHAR", "NO"),
            ("report_date", "DATE", "NO"),
            ("metadata_run_id", "VARCHAR", "NO"),
            ("submissions_sha256", "VARCHAR", "NO"),
            ("extraction_status", "VARCHAR", "NO"),
            ("fiscal_year_focus", "INTEGER", "YES"),
            ("fiscal_period_focus", "VARCHAR", "YES"),
            ("document_period_end_date", "DATE", "YES"),
            ("occurrences_json", "VARCHAR", "NO"),
            ("issues_json", "VARCHAR", "NO"),
        )
        assert c.execute(
            "SELECT constraint_column_names FROM duckdb_constraints() WHERE table_name='filing_fiscal_metadata' AND constraint_type='PRIMARY KEY'"
        ).fetchall() == [
            (
                [
                    "cik",
                    "accession_number",
                    "source_document_name",
                    "source_sha256",
                    "extraction_version",
                ],
            )
        ]
    stored = rows(paths[2])
    occurrences = json.loads(stored[0][13])
    assert [o["source_occurrence_id"] for o in occurrences] == [
        o.source_occurrence_id for o in result.metadata.occurrences
    ]
    assert occurrences[0]["raw_value"] == " 2024\n"
    assert occurrences[0]["context_xml"] == result.metadata.occurrences[0].context_xml
    assert occurrences[0]["period_start"] == "2023-10-01"
    assert occurrences[0]["period_end"] == "2024-09-28"
    assert set(occurrences[0]) == set(
        result.metadata.occurrences[0].__dataclass_fields__
    )
    assert stored[0][14] == "[]"
    # JSON object spacing/order does not alter evidence equality or the existing row.
    with duckdb.connect(str(paths[2])) as c:
        c.execute(
            "UPDATE silver.filing_fiscal_metadata SET occurrences_json=?",
            [json.dumps(occurrences, indent=4)],
        )
    before = rows(paths[2])
    again = load(paths)
    assert again.outcome == "ALREADY_EXISTS" and rows(paths[2]) == before
    assert extractor.call_count == 2


@pytest.mark.parametrize("kind", ["conflict", "invalid-duplicate", "missing"])
def test_partial_evidence(tmp_path: Path, kind: str) -> None:
    if kind == "conflict":
        xml = document(fact(CONCEPTS[0], "2023"))
    elif kind == "invalid-duplicate":
        xml = document(fact(CONCEPTS[0], "bad"))
    else:
        from test_silver_extraction import duration_context, xbrl

        xml = xbrl(duration_context("year", "2023-10-01"))
    result = load(make_fixture(tmp_path, xml))
    assert result.status == "PARTIAL"
    stored = rows(result.database_path)[0]
    issues = json.loads(stored[14])
    occurrences = json.loads(stored[13])
    assert issues and set(issues[0]) == {
        "field_name",
        "reason_code",
        "message",
        "source_occurrence_ids",
    }
    if kind == "conflict":
        assert stored[10] is None and len(issues[0]["source_occurrence_ids"]) == 2
    if kind == "invalid-duplicate":
        assert stored[10:13] == (2024, "FY", result.metadata.report_date)
        assert (
            occurrences[-1]["normalized_value"] is None
            and occurrences[-1]["context_xml"]
        )
    if kind == "missing":
        assert issues[0]["source_occurrence_ids"] == [] and occurrences == []
    assert (
        load_filing_fiscal_metadata(
            tmp_path
            / "bronze"
            / f"cik={result.metadata.reference.cik}"
            / f"accession={ACCESSION}",
            tmp_path
            / "bronze"
            / f"cik={result.metadata.reference.cik}"
            / f"accession={ACCESSION}"
            / "manifests/run_id=test-run.json",
            database_path=result.database_path,
        ).outcome
        == "ALREADY_EXISTS"
    )


@pytest.mark.parametrize(
    "field",
    [
        "form",
        "report_date",
        "metadata_run_id",
        "submissions_sha256",
        "extraction_status",
        "fiscal_year_focus",
        "fiscal_period_focus",
        "document_period_end_date",
    ],
)
def test_scalar_conflicts_preserve_row(tmp_path: Path, field: str) -> None:
    paths = make_fixture(tmp_path, document())
    load(paths)
    value = {
        "report_date": "2024-09-27",
        "document_period_end_date": "2024-09-27",
        "fiscal_year_focus": 2023,
    }.get(field, "different")
    with duckdb.connect(str(paths[2])) as c:
        c.execute(f"UPDATE silver.filing_fiscal_metadata SET {field}=?", [value])
    before = rows(paths[2])
    with pytest.raises(FiscalMetadataLoadError, match=field):
        load(paths)
    assert rows(paths[2]) == before


@pytest.mark.parametrize(
    "kind", ["raw", "order", "issues", "malformed", "object", "nan", "duplicate-key"]
)
def test_evidence_conflicts_and_malformed_json(tmp_path: Path, kind: str) -> None:
    paths = make_fixture(tmp_path, document())
    load(paths)
    records = json.loads(rows(paths[2])[0][13])
    field = "occurrences_json"
    if kind == "raw":
        records[0]["raw_value"] = "different"
        value = json.dumps(records)
    elif kind == "order":
        value = json.dumps(records[::-1])
    elif kind == "issues":
        field = "issues_json"
        value = '[{"field_name":"changed"}]'
    else:
        value = {
            "malformed": "[",
            "object": "{}",
            "nan": "[NaN]",
            "duplicate-key": '[{"x":1,"x":2}]',
        }[kind]
    with duckdb.connect(str(paths[2])) as c:
        c.execute(f"UPDATE silver.filing_fiscal_metadata SET {field}=?", [value])
    before = rows(paths[2])
    with pytest.raises(FiscalMetadataLoadError, match=field):
        load(paths)
    assert rows(paths[2]) == before


def replace_document(paths: tuple[Path, Path, Path], xml: bytes) -> None:
    (paths[0] / "sec-derived" / DOCUMENT_NAME).write_bytes(xml)
    manifest = json.loads(paths[1].read_text())
    entry = next(r for r in manifest["files"] if r["document_name"] == DOCUMENT_NAME)
    entry.update(size_bytes=len(xml), sha256=hashlib.sha256(xml).hexdigest())
    paths[1].write_text(json.dumps(manifest))
    with duckdb.connect(str(paths[2])) as c:
        c.execute("UPDATE silver.active_filings SET source_sha256=?", [entry["sha256"]])


def test_historical_sources_and_versions_preserved(tmp_path: Path) -> None:
    paths = make_fixture(tmp_path, document())
    load(paths)
    first = rows(paths[2])[0]
    with duckdb.connect(str(paths[2])) as c:
        old = list(first)
        old[4] = "0"
        storage._insert_row(c, tuple(old))
    replace_document(paths, document(year="2025"))
    result = load(paths)
    assert result.outcome == "INSERTED" and result.metadata.fiscal_year_focus == 2025
    assert len(rows(paths[2])) == 3 and first in rows(paths[2])
    assert load(paths).outcome == "ALREADY_EXISTS"


def test_separate_accession(tmp_path: Path) -> None:
    paths = make_fixture(tmp_path / "first", document())
    load(paths)
    other = make_fixture(tmp_path / "second", document())
    accession = "0000320193-24-000124"
    target = other[0].with_name("accession=" + accession)
    other[0].rename(target)
    manifest = target / "manifests" / other[1].name
    discovery = target / "metadata/discovery.json"
    record = json.loads(discovery.read_text())
    record["accession_number"] = accession
    discovery.write_text(json.dumps(record))
    record = json.loads(manifest.read_text())
    record["accession_number"] = accession
    entry = next(r for r in record["files"] if r["document_name"] == "discovery.json")
    entry.update(
        size_bytes=discovery.stat().st_size,
        sha256=hashlib.sha256(discovery.read_bytes()).hexdigest(),
    )
    manifest.write_text(json.dumps(record))
    with duckdb.connect(str(paths[2])) as c:
        c.execute(
            "INSERT INTO silver.active_filings SELECT cik,?,source_document_name,source_sha256 FROM silver.active_filings",
            [accession],
        )
        c.execute(
            "INSERT INTO silver.filing_metadata SELECT cik,?,form,report_date,metadata_run_id,submissions_sha256 FROM silver.filing_metadata",
            [accession],
        )
    assert load((target, manifest, paths[2])).outcome == "INSERTED"
    assert len(rows(paths[2])) == 2


@pytest.mark.parametrize(
    "kind", ["view", "columns", "nullability", "key", "missing-db"]
)
def test_missing_database_or_incompatible_relation(tmp_path: Path, kind: str) -> None:
    paths = make_fixture(tmp_path, document())
    if kind == "missing-db":
        with pytest.raises(FiscalMetadataLoadError):
            load((paths[0], paths[1], tmp_path / "absent/database.duckdb"))
        assert not (tmp_path / "absent").exists()
        return
    with duckdb.connect(str(paths[2])) as c:
        if kind == "view":
            c.execute("CREATE VIEW silver.filing_fiscal_metadata AS SELECT 1 AS wrong")
        elif kind == "columns":
            c.execute("CREATE TABLE silver.filing_fiscal_metadata (wrong INTEGER)")
        else:
            columns = ", ".join(
                n
                + " "
                + t
                + (
                    " NOT NULL"
                    if nullable == "NO" and not (kind == "nullability" and n == "form")
                    else ""
                )
                for n, t, nullable in storage._SCHEMA
            )
            key = (
                ", PRIMARY KEY (" + ", ".join(storage._KEY) + ")"
                if kind == "nullability"
                else ", PRIMARY KEY(cik,accession_number)"
            )
            c.execute(
                "CREATE TABLE silver.filing_fiscal_metadata (" + columns + key + ")"
            )
    with pytest.raises(FiscalMetadataLoadError, match="Incompatible"):
        load(paths)
    with duckdb.connect(str(paths[2])) as c:
        assert (
            c.execute("SELECT count(*) FROM silver.filing_fiscal_metadata").fetchone()
            == (0,)
            if kind != "view"
            else True
        )


@pytest.mark.parametrize("relation", ["active_filings", "filing_metadata"])
def test_catalog_changed_after_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relation: str
) -> None:
    paths = make_fixture(tmp_path, document())
    original = storage.extract_filing_fiscal_metadata

    def changed(*args: Any, **kwargs: Any):
        metadata = original(*args, **kwargs)
        with duckdb.connect(str(paths[2])) as c:
            column = (
                "source_sha256" if relation == "active_filings" else "metadata_run_id"
            )
            c.execute(f"UPDATE silver.{relation} SET {column}='changed'")
        return metadata

    monkeypatch.setattr(storage, "extract_filing_fiscal_metadata", changed)
    with pytest.raises(FiscalMetadataLoadError, match="changed"):
        load(paths)
    with duckdb.connect(str(paths[2])) as c:
        assert c.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='filing_fiscal_metadata'"
        ).fetchone() == (0,)


@pytest.mark.parametrize("error", [duckdb.ConstraintException, RuntimeError])
def test_insert_failure_rolls_back_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    paths = make_fixture(tmp_path, document())
    load(paths)
    before = rows(paths[2])
    replace_document(paths, document(year="2025"))

    def fail(connection: duckdb.DuckDBPyConnection, row: tuple[Any, ...]) -> None:
        connection.execute(
            "UPDATE silver.filing_fiscal_metadata SET fiscal_year_focus=1"
        )
        raise error("injected failure")

    monkeypatch.setattr(storage, "_insert_row", fail)
    with pytest.raises(
        FiscalMetadataLoadError if error is duckdb.ConstraintException else RuntimeError
    ) as caught:
        load(paths)
    if error is duckdb.ConstraintException:
        assert isinstance(caught.value.__cause__, duckdb.Error)
    assert rows(paths[2]) == before


def test_readback_failure_rolls_back_table_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = make_fixture(tmp_path, document())
    original = storage._insert_row

    def changed(connection: duckdb.DuckDBPyConnection, row: tuple[Any, ...]) -> None:
        original(connection, row)
        connection.execute("UPDATE silver.filing_fiscal_metadata SET issues_json='{}'")

    monkeypatch.setattr(storage, "_insert_row", changed)
    with pytest.raises(FiscalMetadataLoadError, match="issues_json"):
        load(paths)
    with duckdb.connect(str(paths[2]), read_only=True) as c:
        assert c.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='filing_fiscal_metadata'"
        ).fetchone() == (0,)
