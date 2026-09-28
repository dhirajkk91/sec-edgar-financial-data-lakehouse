from datetime import date
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock
from uuid import UUID

import pytest
from test_company_run import pipeline_result

from sec_edgar_lakehouse import CompanyRunError, CompanyRunResult
from sec_edgar_lakehouse import company_pipeline_run as cli


def arguments(tmp_path: Path) -> list[str]:
    return [
        "--cik",
        "320193",
        "--forms",
        "10-Q",
        "10-K",
        "--bronze-directory",
        str(tmp_path / "bronze space"),
        "--silver-directory",
        str(tmp_path / "silver"),
        "--database",
        str(tmp_path / "catalog.duckdb"),
        "--run-directory",
        str(tmp_path / "runs"),
    ]


@pytest.mark.parametrize(
    "status,code", [("COMPLETE", 0), ("PARTIAL", 2), ("FAILED", 1)]
)
@pytest.mark.parametrize("explicit", [True, False])
def test_summary_and_forwarding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    code: int,
    explicit: bool,
) -> None:
    agent = "Test contact@example.org"
    monkeypatch.setenv("SEC_USER_AGENT", agent)
    record = tmp_path / "runs/cik=0000320193/run_id=run/run.json"
    execute = Mock(
        return_value=CompanyRunResult(
            "run", cast(Any, status), pipeline_result(status), record, None, None
        )
    )
    monkeypatch.setattr(cli, "execute_company_pipeline_run", execute)
    args = arguments(tmp_path) + [
        "--filed-on-or-after",
        "2024-01-01",
        "--filed-on-or-before",
        "2025-12-31",
        "--limit",
        "8",
        "--request-interval-seconds",
        "0.75",
    ]
    if explicit:
        args += ["--run-id", "manual-apple-001"]
    assert cli.main(args) == code
    execute.assert_called_once()
    kwargs = execute.call_args.kwargs
    run_id = kwargs["processing_run_id"]
    if explicit:
        assert run_id == "manual-apple-001"
    else:
        assert UUID(run_id).hex == run_id and UUID(run_id).version == 4
    execute.assert_called_once_with(
        "320193",
        user_agent=agent,
        forms=["10-Q", "10-K"],
        bronze_directory=tmp_path / "bronze space",
        silver_directory=tmp_path / "silver",
        database_path=tmp_path / "catalog.duckdb",
        run_directory=tmp_path / "runs",
        processing_run_id=run_id,
        filed_on_or_after=date(2024, 1, 1),
        filed_on_or_before=date(2025, 12, 31),
        limit=8,
        request_interval_seconds=0.75,
    )
    output = capsys.readouterr()
    assert not output.err
    for expected in [
        "Run ID: run",
        "Apple Inc.",
        f"Pipeline status: {status}",
        f"Processing status: {status}",
        "Selected: 5",
        "Complete: 1",
        "Partial: 1",
        "Skipped: 1",
        "Failed: 1",
        "Not attempted: 1",
        "Catalog status: COMPLETE",
        "Database: catalog.duckdb",
        "Facts: 3",
        "Fact dimensions: 2",
        "Rejected facts: 1",
        str(record),
    ]:
        assert expected in output.out
    assert agent not in output.out and "contact@example.org" not in output.out


@pytest.mark.parametrize("value", [None, "", " \t\n"])
def test_missing_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    else:
        monkeypatch.setenv("SEC_USER_AGENT", value)
    execute = Mock()
    monkeypatch.setattr(cli, "execute_company_pipeline_run", execute)
    assert cli.main(arguments(tmp_path)) == 1
    execute.assert_not_called()
    assert "SEC_USER_AGENT" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    ["20250101", "2025-1-01", "2025-02-30", "2025-W01-1", "2025-01-01T00:00:00"],
)
def test_invalid_dates_use_argparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    execute = Mock()
    monkeypatch.setattr(cli, "execute_company_pipeline_run", execute)
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments(tmp_path) + ["--filed-on-or-after", value])
    assert caught.value.code == 1
    assert "error:" in capsys.readouterr().err
    execute.assert_not_called()


@pytest.mark.parametrize("recorded", [True, False])
def test_execution_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: bool,
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "Test contact@example.org")
    execute = (
        Mock(
            return_value=CompanyRunResult(
                "run",
                "FAILED",
                None,
                tmp_path / "runs/cik=0000320193/run_id=run/run.json",
                "PROCESSING",
                "discovery failed",
            )
        )
        if recorded
        else Mock(side_effect=CompanyRunError("disk full"))
    )
    monkeypatch.setattr(cli, "execute_company_pipeline_run", execute)
    assert cli.main(arguments(tmp_path)) == 1
    assert execute.call_args.kwargs["request_interval_seconds"] == 0.5
    output = capsys.readouterr()
    assert ("discovery failed" if recorded else "disk full") in output.err
    assert "Traceback" not in output.err
    assert "contact@example.org" not in output.err
    if recorded:
        assert str(tmp_path / "runs/cik=0000320193/run_id=run/run.json") in output.out
