from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock
from uuid import UUID

import pytest
from test_end_to_end_run import pipeline_result

from sec_edgar_lakehouse import EndToEndRunError, EndToEndRunResult
from sec_edgar_lakehouse import end_to_end_pipeline_run as cli


def arguments(root: Path) -> list[str]:
    return [
        "--cik",
        "320193",
        "--forms",
        "10-Q",
        "10-K",
        "--bronze-directory",
        str(root / "bronze space"),
        "--silver-directory",
        str(root / "silver"),
        "--database",
        str(root / "catalog.duckdb"),
        "--run-directory",
        str(root / "company runs"),
        "--end-to-end-run-directory",
        str(root / "final runs"),
        "--dbt-project-directory",
        str(root / "dbt project"),
        "--dbt-profiles-directory",
        str(root / "dbt profiles"),
        "--dbt-artifact-directory",
        str(root / "fresh artifacts"),
    ]


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Mock:
    execute = Mock(
        return_value=EndToEndRunResult(
            "returned-id",
            "COMPLETE",
            pipeline_result(tmp_path),
            tmp_path / "final runs/cik=0000320193/run_id=returned-id.json",
            None,
            None,
        )
    )
    monkeypatch.setattr(cli, "execute_end_to_end_pipeline_run", execute)
    monkeypatch.setenv("SEC_USER_AGENT", "PipelineCLI contact@example.org")
    return execute


@pytest.mark.parametrize(
    "status,code", [("COMPLETE", 0), ("PARTIAL", 2), ("FAILED", 1)]
)
def test_status_exit_codes_exact_forwarding_and_evidence_output(
    tmp_path: Path,
    api: Mock,
    capsys: pytest.CaptureFixture[str],
    status: str,
    code: int,
) -> None:
    result = api.return_value
    api.return_value = replace(result, status=cast(Any, status))
    args = arguments(tmp_path) + [
        "--run-id",
        "provided-id",
        "--filed-on-or-after",
        "2025-01-01",
        "--filed-on-or-before",
        "2025-12-31",
        "--limit",
        "2",
        "--request-interval-seconds",
        "0.75",
        "--dbt-timeout-seconds",
        "120",
    ]
    assert cli.main(args) == code
    api.assert_called_once_with(
        "320193",
        user_agent="PipelineCLI contact@example.org",
        forms=["10-Q", "10-K"],
        bronze_directory=tmp_path / "bronze space",
        silver_directory=tmp_path / "silver",
        database_path=tmp_path / "catalog.duckdb",
        run_directory=tmp_path / "company runs",
        end_to_end_run_directory=tmp_path / "final runs",
        processing_run_id="provided-id",
        dbt_project_directory=tmp_path / "dbt project",
        dbt_profiles_directory=tmp_path / "dbt profiles",
        dbt_artifact_directory=tmp_path / "fresh artifacts",
        filed_on_or_after=date(2025, 1, 1),
        filed_on_or_before=date(2025, 12, 31),
        limit=2,
        request_interval_seconds=0.75,
        dbt_timeout_seconds=120.0,
    )
    output = capsys.readouterr()
    assert not output.err
    for text in (
        "Run ID: returned-id",
        f"Pipeline status: {status}",
        "Company status: COMPLETE",
        "Metadata status: COMPLETE",
        "dbt status: COMPLETE",
        "Selected: 1",
        "Usable: 1",
        "Catalog status: COMPLETE",
        "Filing metadata: COMPLETE",
        "Fiscal metadata:",
        "INSERTED",
        "dbt invocation: fixture-invocation",
        "dbt nodes: success=1",
        str(result.run_record_path),
        "Company record:",
        "dbt artifacts:",
        "dbt stdout:",
        "dbt stderr:",
        "dbt manifest:",
        "dbt run results:",
    ):
        assert text in output.out
    assert "contact@example.org" not in output.out


def test_uuid_and_defaults(tmp_path: Path, api: Mock) -> None:
    assert cli.main(arguments(tmp_path)) == 0
    api.assert_called_once()
    values = api.call_args.kwargs
    identifier = UUID(values["processing_run_id"])
    assert identifier.version == 4 and identifier.hex == values["processing_run_id"]
    assert values["request_interval_seconds"] == 0.5
    assert values["dbt_timeout_seconds"] == 600.0
    assert (
        values["filed_on_or_after"]
        is values["filed_on_or_before"]
        is values["limit"]
        is None
    )


@pytest.mark.parametrize("agent", [None, "", " \t\n"])
def test_missing_or_blank_environment_never_calls_api(
    tmp_path: Path,
    api: Mock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agent: str | None,
) -> None:
    if agent is None:
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    else:
        monkeypatch.setenv("SEC_USER_AGENT", agent)
    assert cli.main(arguments(tmp_path)) == 1
    api.assert_not_called()
    assert "SEC_USER_AGENT" in capsys.readouterr().err


@pytest.mark.parametrize(
    "option",
    [
        "--cik",
        "--forms",
        "--bronze-directory",
        "--silver-directory",
        "--database",
        "--run-directory",
        "--end-to-end-run-directory",
        "--dbt-project-directory",
        "--dbt-profiles-directory",
        "--dbt-artifact-directory",
    ],
)
def test_required_arguments_fail_without_execution(
    tmp_path: Path, api: Mock, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    args = arguments(tmp_path)
    index = args.index(option)
    del args[index : index + (3 if option == "--forms" else 2)]
    with pytest.raises(SystemExit) as raised:
        cli.main(args)
    assert raised.value.code == 1
    assert option in capsys.readouterr().err
    api.assert_not_called()


@pytest.mark.parametrize(
    "value",
    ["20250101", "2025-1-01", "2025-02-30", "2025-W01-1", "2025-01-01T00:00:00"],
)
def test_strict_iso_dates_without_execution(
    tmp_path: Path, api: Mock, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(arguments(tmp_path) + ["--filed-on-or-after", value])
    assert raised.value.code == 1
    assert "error:" in capsys.readouterr().err
    api.assert_not_called()


def test_user_agent_argument_is_not_supported_or_exposed(
    tmp_path: Path, api: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(
            arguments(tmp_path) + ["--user-agent", "PipelineCLI contact@example.org"]
        )
    assert raised.value.code == 1
    output = capsys.readouterr()
    assert "contact@example.org" not in output.err and "[redacted]" in output.err
    api.assert_not_called()


@pytest.mark.parametrize("retained", [False, True])
def test_persistence_and_input_errors_print_retained_evidence_safely(
    tmp_path: Path, api: Mock, capsys: pytest.CaptureFixture[str], retained: bool
) -> None:
    returned = api.return_value
    api.side_effect = EndToEndRunError(
        "disk failure PipelineCLI contact@example.org other@example.net",
        pipeline_result=returned.pipeline_result if retained else None,
        run_record_path=returned.run_record_path if retained else None,
    )
    assert cli.main(arguments(tmp_path) + ["--run-id", "provided-id"]) == 1
    output = capsys.readouterr()
    assert "Run ID: provided-id" in output.out
    assert "FAILED: disk failure" in output.err
    assert "@" not in output.err and "[redacted]" in output.err
    if retained:
        assert "Company record:" in output.out and "dbt artifacts:" in output.out
        assert f"Intended final record: {returned.run_record_path}" in output.out
    else:
        assert "Intended final record:" not in output.out
    assert "\nFinal record:" not in output.out


def test_coordinator_preflight_failure_has_final_record_and_unavailable_stages(
    tmp_path: Path, api: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    api.return_value = replace(
        api.return_value,
        status="FAILED",
        pipeline_result=None,
        error_stage="PREFLIGHT",
        error_message="project unavailable contact@example.org",
    )
    assert cli.main(arguments(tmp_path)) == 1
    output = capsys.readouterr()
    assert "Pipeline status: FAILED" in output.out
    assert "stages: unavailable" in output.out and "Final record:" in output.out
    assert "FAILED (PREFLIGHT): project unavailable [redacted]" in output.err


def test_stage_failure_skip_reasons_and_internal_diagnostics_are_printed(
    tmp_path: Path, api: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    pipeline = api.return_value.pipeline_result
    assert pipeline is not None and pipeline.company_run is not None
    company = replace(
        pipeline.company_run,
        error_stage="PROCESSING",
        error_message="retained processing issue contact@example.org",
    )
    pipeline = replace(
        pipeline,
        status="PARTIAL",
        company_run=company,
        metadata_status="FAILED",
        metadata_result=None,
        metadata_error="metadata identity failed contact@example.org",
        dbt_status="SKIPPED",
        dbt_result=None,
        dbt_artifact_directory=None,
        dbt_skip_reason="metadata failed contact@example.org",
    )
    api.return_value = replace(
        api.return_value, status="PARTIAL", pipeline_result=pipeline
    )
    assert cli.main(arguments(tmp_path)) == 2
    output = capsys.readouterr()
    assert (
        "Metadata status: FAILED" in output.out and "dbt status: SKIPPED" in output.out
    )
    assert "retained processing issue" in output.err and "Metadata error:" in output.err
    assert "dbt skipped: metadata failed" in output.err
    assert "@" not in output.err and "dbt artifacts:" not in output.out


def test_returned_dbt_failure_prints_build_error_and_node_counts(
    tmp_path: Path, api: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    pipeline = api.return_value.pipeline_result
    assert pipeline is not None and pipeline.dbt_result is not None
    built = replace(
        pipeline.dbt_result,
        status="FAILED",
        node_status_counts=(("error", 1),),
        error_message="failed model contact@example.org",
    )
    api.return_value = replace(
        api.return_value,
        status="PARTIAL",
        pipeline_result=replace(
            pipeline, status="PARTIAL", dbt_status="FAILED", dbt_result=built
        ),
    )
    assert cli.main(arguments(tmp_path)) == 2
    output = capsys.readouterr()
    assert "dbt status: FAILED" in output.out and "dbt nodes: error=1" in output.out
    assert "dbt build error: failed model [redacted]" in output.err


def test_unexpected_errors_propagate(tmp_path: Path, api: Mock) -> None:
    error = RuntimeError("unexpected programming error")
    api.side_effect = error
    with pytest.raises(RuntimeError) as raised:
        cli.main(arguments(tmp_path))
    assert raised.value is error


def test_help_does_not_execute(api: Mock, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])
    assert raised.value.code == 0
    assert "--end-to-end-run-directory" in capsys.readouterr().out
    api.assert_not_called()
