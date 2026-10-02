from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import pytest

from sec_edgar_lakehouse import (
    FilingMetadataError,
    FilingMetadataIssue,
    FilingMetadataLoadResult,
)
from sec_edgar_lakehouse import filing_metadata_load as cli


@pytest.mark.parametrize("status,code", [("COMPLETE", 0), ("PARTIAL", 2)])
def test_cli_forwarding_summary_and_issues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: Literal["COMPLETE", "PARTIAL"],
    code: int,
) -> None:
    path = tmp_path / "run.json"
    database = tmp_path / "catalog.duckdb"
    issues = (
        (
            FilingMetadataIssue(
                "0000000001",
                "0000000001-25-000001",
                "MISSING_METADATA",
                "Not in snapshot",
            ),
            FilingMetadataIssue(
                "0000000001",
                "0000000001-25-000002",
                "METADATA_CONFLICT",
                "Conflicting fields: form",
            ),
        )
        if status == "PARTIAL"
        else ()
    )
    result = FilingMetadataLoadResult(
        status,
        database,
        "0000000001",
        "run-one",
        4 if issues else 2,
        1,
        1,
        1 if issues else 0,
        1 if issues else 0,
        issues,
    )
    load = Mock(return_value=result)
    monkeypatch.setattr(cli, "load_filing_metadata", load)
    assert cli.main(["--run-record", str(path), "--database", str(database)]) == code
    load.assert_called_once_with(run_record_path=path, database_path=database)
    output = capsys.readouterr()
    assert output.err == ""
    for text in [
        status,
        "0000000001",
        "run-one",
        str(database),
        f"Active filings: {result.active_filing_count}",
        "Inserted: 1",
        "Already existing: 1",
        f"Missing: {result.missing_count}",
        f"Conflicts: {result.conflict_count}",
    ]:
        assert text in output.out
    for issue in issues:
        assert (
            f"{issue.accession_number} | {issue.reason_code}: {issue.message}"
            in output.out
        )


def test_expected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    load = Mock(side_effect=FilingMetadataError("invalid evidence"))
    monkeypatch.setattr(cli, "load_filing_metadata", load)
    assert (
        cli.main(
            [
                "--run-record",
                str(tmp_path / "run.json"),
                "--database",
                str(tmp_path / "catalog.duckdb"),
            ]
        )
        == 1
    )
    load.assert_called_once()
    output = capsys.readouterr()
    assert output.out == "" and output.err == "FAILED: invalid evidence\n"


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--run-record", "run.json"],
        ["--database", "database.duckdb"],
        ["--unknown"],
    ],
)
def test_invalid_arguments(args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    load = Mock()
    monkeypatch.setattr(cli, "load_filing_metadata", load)
    with pytest.raises(SystemExit) as caught:
        cli.main(args)
    assert caught.value.code == 1
    load.assert_not_called()


def test_programming_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli, "load_filing_metadata", Mock(side_effect=RuntimeError("bug"))
    )
    with pytest.raises(RuntimeError, match="bug"):
        cli.main(
            [
                "--run-record",
                str(tmp_path / "run.json"),
                "--database",
                str(tmp_path / "catalog.duckdb"),
            ]
        )
