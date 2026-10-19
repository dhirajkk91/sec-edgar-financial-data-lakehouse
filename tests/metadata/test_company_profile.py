"""Company profiles are read from saved SEC responses and stored as snapshots.

The fixtures write real response bytes with their recorded checksum, so the
loader's evidence checks run exactly as they do on pipeline output.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from sec_edgar_lakehouse import (
    CompanyProfileError,
    load_company_profile,
)
from sec_edgar_lakehouse.metadata import company_profile_load

CIK = "0000320193"


def response(**changes: Any) -> dict[str, Any]:
    """A trimmed SEC submissions response with the company fields the loader reads."""
    data: dict[str, Any] = {
        "cik": CIK,
        "entityType": "operating",
        "sic": "3571",
        "sicDescription": "Electronic Computers",
        "ownerOrg": "06 Technology",
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "category": "Large accelerated filer",
        "fiscalYearEnd": "0926",
        "stateOfIncorporation": "CA",
        "filings": {"recent": {}, "files": []},
    }
    data.update(changes)
    return data


def run_record(
    root: Path,
    data: dict[str, Any] | None = None,
    run_id: str = "run-one",
    retrieved_at: str = "2026-01-01T00:00:00+00:00",
) -> Path:
    """Save response bytes and a run record that names their size and checksum."""
    directory = root / "runs" / f"cik={CIK}" / f"run_id={run_id}"
    directory.mkdir(parents=True)
    content = json.dumps(response() if data is None else data).encode()
    (directory / "submissions.json").write_bytes(content)
    path = directory / "run.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "cik": CIK,
                "run_id": run_id,
                "submissions_evidence": {
                    "path": "submissions.json",
                    "url": f"https://data.sec.gov/submissions/CIK{CIK}.json",
                    "retrieved_at": retrieved_at,
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            }
        )
    )
    return path


def database(root: Path) -> Path:
    """An existing, empty DuckDB file, like the catalog the pipeline creates."""
    path = root / "catalog.duckdb"
    duckdb.connect(str(path)).close()
    return path


def stored(path: Path) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(path), read_only=True) as connection:
        return connection.execute(
            "SELECT cik, company_name, tickers, exchanges, sic_code, sic_description, "
            "owner_org, entity_type, filer_category, fiscal_year_end, "
            "state_of_incorporation, profile_run_id FROM silver.company_profiles "
            "ORDER BY submissions_retrieved_at"
        ).fetchall()


def test_load_stores_profile_and_rerun_reuses_it(tmp_path: Path) -> None:
    record = run_record(tmp_path)
    db = database(tmp_path)
    first = load_company_profile(run_record_path=record, database_path=db)
    second = load_company_profile(run_record_path=record, database_path=db)
    assert (first.outcome, second.outcome) == ("INSERTED", "ALREADY_EXISTS")
    assert (
        first.profile.submissions_sha256
        == hashlib.sha256((record.parent / "submissions.json").read_bytes()).hexdigest()
    )
    assert stored(db) == [
        (
            CIK,
            "Apple Inc.",
            ["AAPL"],
            ["Nasdaq"],
            "3571",
            "Electronic Computers",
            "06 Technology",
            "operating",
            "Large accelerated filer",
            "0926",
            "CA",
            "run-one",
        )
    ]


def test_newer_response_adds_a_snapshot(tmp_path: Path) -> None:
    db = database(tmp_path)
    load_company_profile(run_record_path=run_record(tmp_path), database_path=db)
    newer = run_record(
        tmp_path,
        response(sicDescription="Electronic Computers (renamed)"),
        run_id="run-two",
        retrieved_at="2026-02-01T00:00:00+00:00",
    )
    result = load_company_profile(run_record_path=newer, database_path=db)
    assert result.outcome == "INSERTED"
    assert [row[5] for row in stored(db)] == [
        "Electronic Computers",
        "Electronic Computers (renamed)",
    ]


def test_empty_sec_fields_become_null_not_guesses(tmp_path: Path) -> None:
    data = response(tickers=[], exchanges=[], sic="", ownerOrg=None, cik=320193)
    del data["fiscalYearEnd"]
    db = database(tmp_path)
    load_company_profile(run_record_path=run_record(tmp_path, data), database_path=db)
    row = stored(db)[0]
    assert row[2:7] == ([], [], None, "Electronic Computers", None)
    assert row[9] is None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (response(cik="0000789019"), "CIK does not match"),
        (response(name="  "), "no company name"),
        (response(sic="ABC"), "SIC code"),
        (response(fiscalYearEnd="Sept"), "MMDD"),
        (response(tickers="AAPL"), "list of text"),
        (response(ownerOrg=6), "must be text"),
    ],
)
def test_invalid_response_fields_fail_before_writing(
    tmp_path: Path, data: dict[str, Any], message: str
) -> None:
    db = database(tmp_path)
    with pytest.raises(CompanyProfileError, match=message):
        load_company_profile(
            run_record_path=run_record(tmp_path, data), database_path=db
        )
    with duckdb.connect(str(db), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='company_profiles'"
        ).fetchone() == (0,)


def test_changed_response_bytes_are_rejected(tmp_path: Path) -> None:
    record = run_record(tmp_path)
    (record.parent / "submissions.json").write_text(json.dumps(response(name="Edited")))
    with pytest.raises(CompanyProfileError, match="checksum"):
        load_company_profile(run_record_path=record, database_path=database(tmp_path))


def test_run_record_must_match_its_directories(tmp_path: Path) -> None:
    record = run_record(tmp_path)
    content = json.loads(record.read_text())
    content["run_id"] = "another-run"
    record.write_text(json.dumps(content))
    with pytest.raises(CompanyProfileError, match="evidence directories"):
        load_company_profile(run_record_path=record, database_path=database(tmp_path))


def test_missing_database_and_incompatible_table_fail(tmp_path: Path) -> None:
    record = run_record(tmp_path)
    with pytest.raises(CompanyProfileError, match="database error"):
        load_company_profile(
            run_record_path=record, database_path=tmp_path / "missing.duckdb"
        )
    db = database(tmp_path)
    with duckdb.connect(str(db)) as connection:
        connection.execute("CREATE SCHEMA silver")
        connection.execute("CREATE TABLE silver.company_profiles (cik VARCHAR)")
    with pytest.raises(CompanyProfileError, match="Incompatible"):
        load_company_profile(run_record_path=record, database_path=db)


def test_cli_loads_each_record_and_reports_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = run_record(tmp_path)
    bad = run_record(tmp_path, response(sic="bad"), run_id="run-bad")
    db = database(tmp_path)
    code = company_profile_load.main(
        ["--database", str(db), "--run-record", str(good), str(bad)]
    )
    output = capsys.readouterr()
    assert code == 1
    assert "INSERTED | 0000320193 | Apple Inc. | 06 Technology" in output.out
    assert "Loaded 1 of 2 run records" in output.out
    assert "FAILED" in output.err and "run-bad" in output.err
    assert len(stored(db)) == 1
