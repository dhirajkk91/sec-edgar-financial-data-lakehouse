from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, call

import httpx
import pytest
from test_company_metadata import company_run as make_company_run
from test_company_metadata import filing_result, fiscal_result, selected

from sec_edgar_lakehouse import (
    CompanyFiscalMetadataResult,
    CompanyMetadataLoadError,
    CompanyMetadataLoadResult,
    CompanyRunError,
    CompanyRunResult,
    DbtBuildError,
    DbtBuildResult,
    EndToEndPipelineError,
    EndToEndPipelineResult,
    run_end_to_end_pipeline,
)
from sec_edgar_lakehouse import end_to_end_pipeline as pipeline


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Any]:
    project, profiles = tmp_path / "dbt project", tmp_path / "dbt profiles"
    project.mkdir()
    profiles.mkdir()
    (project / "dbt_project.yml").write_text("name: fixture\n")
    (profiles / "profiles.yml").write_text("fixture: {}\n")
    return {
        "cik": "320193",
        "user_agent": "Pipeline acceptance contact@example.org",
        "forms": ("10-K", "10-Q"),
        "bronze_directory": tmp_path / "managed alias" / ".." / "bronze",
        "silver_directory": tmp_path / "managed alias" / ".." / "silver",
        "database_path": tmp_path / "database alias" / ".." / "catalog.duckdb",
        "processing_run_id": "metadata-run",
        "run_directory": tmp_path / "managed alias" / ".." / "runs",
        "dbt_project_directory": project,
        "dbt_profiles_directory": profiles,
        "dbt_artifact_directory": tmp_path / "fresh dbt artifacts",
        "filed_on_or_after": date(2024, 1, 1),
        "filed_on_or_before": date(2025, 12, 31),
        "limit": 2,
        "client": None,
        "request_interval_seconds": 0.75,
        "dbt_timeout_seconds": 45.0,
    }


def company_result(
    root: Path,
    *,
    status: str = "COMPLETE",
    outcomes: tuple[str, ...] = ("COMPLETE",),
    catalog_status: str = "COMPLETE",
) -> CompanyRunResult:
    run = make_company_run(root, outcomes, catalog_status)
    assert run.pipeline_result is not None
    company_pipeline = replace(
        run.pipeline_result,
        status=cast(Any, status),
        processing=replace(run.pipeline_result.processing, status=cast(Any, status)),
    )
    return replace(run, status=cast(Any, status), pipeline_result=company_pipeline)


def metadata_result(
    run: CompanyRunResult,
    root: Path,
    *,
    status: str = "COMPLETE",
    fiscal_outcome: str = "INSERTED",
    fiscal_status: str = "COMPLETE",
) -> CompanyMetadataLoadResult:
    reference = selected(run)[0].filing.reference
    loaded = (
        fiscal_result(root, reference, fiscal_outcome, fiscal_status)
        if fiscal_outcome in {"INSERTED", "ALREADY_EXISTS"}
        else None
    )
    fiscal = CompanyFiscalMetadataResult(
        reference,
        cast(Any, fiscal_outcome),
        loaded,
        None if loaded else "FISCAL_METADATA",
        None if loaded else "fiscal failure details",
    )
    return CompanyMetadataLoadResult(
        cast(Any, status),
        run,
        root / "catalog.duckdb",
        filing_result(root) if status != "SKIPPED" else None,
        "filing failure details" if status == "FAILED" else None,
        () if status == "SKIPPED" else (fiscal,),
        "metadata loader skip reason" if status == "SKIPPED" else None,
    )


def dbt_result(root: Path, status: str = "COMPLETE") -> DbtBuildResult:
    artifacts = root / "fresh dbt artifacts"
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return DbtBuildResult(
        cast(Any, status),
        root / "catalog.duckdb",
        artifacts,
        timestamp,
        timestamp,
        0 if status == "COMPLETE" else 1,
        "fixture-invocation",
        artifacts / "stdout.log",
        artifacts / "stderr.log",
        artifacts / "target/run_results.json",
        artifacts / "target/manifest.json",
        (("success", 1),) if status == "COMPLETE" else (("error", 1),),
        None if status == "COMPLETE" else "dbt failure details",
    )


@pytest.fixture
def stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock, Mock]:
    run = company_result(tmp_path)
    company = Mock(return_value=run)
    metadata = Mock(return_value=metadata_result(run, tmp_path))
    dbt = Mock(return_value=dbt_result(tmp_path))
    monkeypatch.setattr(pipeline, "execute_company_pipeline_run", company)
    monkeypatch.setattr(pipeline, "load_company_run_metadata", metadata)
    monkeypatch.setattr(pipeline, "run_dbt_build", dbt)
    return company, metadata, dbt


def test_exact_call_order_forwarding_canonical_database_and_stage_identity(
    inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock]
) -> None:
    company, metadata, dbt = stages
    sequence = Mock()
    for name, stage in zip(("company", "metadata", "dbt"), stages, strict=True):
        sequence.attach_mock(stage, name)
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    inputs["client"] = client
    try:
        result = run_end_to_end_pipeline(**inputs)
        assert not client.is_closed
    finally:
        client.close()
    database = inputs["database_path"].resolve()
    forwarded = {
        key: value for key, value in inputs.items() if not key.startswith("dbt_")
    }
    forwarded.pop("cik")
    forwarded["database_path"] = database
    assert sequence.mock_calls == [
        call.company(inputs["cik"], **forwarded),
        call.metadata(company.return_value, database_path=database),
        call.dbt(
            database_path=database,
            project_directory=inputs["dbt_project_directory"].resolve(),
            profiles_directory=inputs["dbt_profiles_directory"].resolve(),
            artifact_directory=inputs["dbt_artifact_directory"].resolve(),
            timeout_seconds=inputs["dbt_timeout_seconds"],
        ),
    ]
    assert result.status == result.metadata_status == result.dbt_status == "COMPLETE"
    assert result.database_path == database
    assert result.company_run is company.return_value
    assert result.metadata_result is metadata.return_value
    assert result.dbt_result is dbt.return_value
    assert result.dbt_result is not None
    assert result.dbt_artifact_directory == result.dbt_result.artifact_directory
    assert result.company_error is result.metadata_error is result.dbt_error is None
    assert result.metadata_skip_reason is result.dbt_skip_reason is None
    assert not inputs["dbt_artifact_directory"].exists()


def test_first_company_run_can_create_absent_database(
    inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock]
) -> None:
    company, metadata, dbt = stages
    database = inputs["database_path"].resolve()
    assert not database.exists()

    def create_catalog(*args: Any, **kwargs: Any) -> CompanyRunResult:
        assert kwargs["database_path"] == database
        assert not database.exists()
        database.write_bytes(b"catalog created by company stage")
        return cast(CompanyRunResult, company.return_value)

    company.side_effect = create_catalog
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "COMPLETE"
    assert database.is_file()
    metadata.assert_called_once_with(company.return_value, database_path=database)
    assert dbt.call_args.kwargs["database_path"] == database


def assert_downstream_skipped(
    result: EndToEndPipelineResult, stages: tuple[Mock, Mock, Mock]
) -> None:
    assert result.metadata_status == result.dbt_status == "SKIPPED"
    assert result.metadata_result is result.dbt_result is None
    assert result.metadata_error is result.dbt_error is None
    assert result.metadata_skip_reason and result.dbt_skip_reason
    assert result.dbt_artifact_directory is None
    stages[1].assert_not_called()
    stages[2].assert_not_called()


def test_empty_complete_selection_is_successful_noop(
    tmp_path: Path, inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock]
) -> None:
    run = company_result(tmp_path, outcomes=(), catalog_status="SKIPPED")
    stages[0].return_value = run
    inputs["database_path"].resolve().write_bytes(
        b"older database must not trigger dbt"
    )
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "COMPLETE" and result.company_run is run
    assert result.company_error is None
    stages[0].assert_called_once()
    assert_downstream_skipped(result, stages)


@pytest.mark.parametrize(
    "case", ["discovery", "processing", "no_usable", "missing_pipeline"]
)
def test_failed_company_or_no_usable_silver_blocks_downstream(
    tmp_path: Path, inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock], case: str
) -> None:
    run = company_result(
        tmp_path,
        status="PARTIAL" if case == "no_usable" else "FAILED",
        outcomes=("FAILED", "NOT_ATTEMPTED"),
        catalog_status="SKIPPED",
    )
    if case in {"discovery", "missing_pipeline"}:
        run = replace(
            run,
            pipeline_result=None,
            status="COMPLETE" if case == "missing_pipeline" else "FAILED",
        )
    stages[0].return_value = run
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "FAILED" and result.company_run is run
    assert result.company_error is None
    assert_downstream_skipped(result, stages)


@pytest.mark.parametrize("catalog_status", ["COMPLETE", "PARTIAL"])
def test_eligible_catalog_with_usable_processing_outcomes_runs_downstream(
    tmp_path: Path,
    inputs: dict[str, Any],
    stages: tuple[Mock, Mock, Mock],
    catalog_status: str,
) -> None:
    run = company_result(
        tmp_path,
        status="PARTIAL",
        outcomes=("COMPLETE", "PARTIAL", "SKIPPED", "FAILED", "NOT_ATTEMPTED"),
        catalog_status=catalog_status,
    )
    stages[0].return_value = run
    stages[1].return_value = metadata_result(run, tmp_path)
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "PARTIAL"
    assert result.metadata_status == result.dbt_status == "COMPLETE"
    for stage in stages:
        stage.assert_called_once()


@pytest.mark.parametrize("catalog_status", ["FAILED", "SKIPPED"])
def test_ineligible_catalog_blocks_old_database_reuse(
    tmp_path: Path,
    inputs: dict[str, Any],
    stages: tuple[Mock, Mock, Mock],
    catalog_status: str,
) -> None:
    run = company_result(tmp_path, status="PARTIAL", catalog_status=catalog_status)
    stages[0].return_value = run
    database = inputs["database_path"].resolve()
    database.write_bytes(b"previous catalog")
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "PARTIAL" and result.company_run is run
    assert database.read_bytes() == b"previous catalog"
    assert_downstream_skipped(result, stages)


@pytest.mark.parametrize("status", ["FAILED", "SKIPPED"])
def test_returned_metadata_failure_or_skip_is_preserved_without_exception_error(
    tmp_path: Path, inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock], status: str
) -> None:
    loaded = metadata_result(
        stages[0].return_value, tmp_path, status=status, fiscal_outcome="FAILED"
    )
    stages[1].return_value = loaded
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "PARTIAL" and result.metadata_status == status
    assert result.metadata_result is loaded
    assert result.metadata_error is None
    assert result.metadata_skip_reason == (
        loaded.skip_reason if status == "SKIPPED" else None
    )
    assert result.dbt_status == "SKIPPED" and result.dbt_skip_reason
    assert (
        result.dbt_result is result.dbt_error is result.dbt_artifact_directory is None
    )
    stages[1].assert_called_once()
    stages[2].assert_not_called()


@pytest.mark.parametrize("fiscal_outcome", ["FAILED", "NOT_ATTEMPTED", "empty"])
def test_metadata_without_successful_fiscal_load_blocks_dbt(
    tmp_path: Path,
    inputs: dict[str, Any],
    stages: tuple[Mock, Mock, Mock],
    fiscal_outcome: str,
) -> None:
    loaded = metadata_result(
        stages[0].return_value,
        tmp_path,
        status="PARTIAL",
        fiscal_outcome="FAILED" if fiscal_outcome == "empty" else fiscal_outcome,
    )
    if fiscal_outcome == "empty":
        loaded = replace(loaded, fiscal_results=())
    stages[1].return_value = loaded
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "PARTIAL" and result.metadata_result is loaded
    assert result.dbt_status == "SKIPPED" and result.dbt_skip_reason
    assert result.dbt_error is result.dbt_result is None
    stages[2].assert_not_called()


@pytest.mark.parametrize(
    "fiscal_outcome,fiscal_status",
    [
        ("INSERTED", "COMPLETE"),
        ("ALREADY_EXISTS", "COMPLETE"),
        ("ALREADY_EXISTS", "PARTIAL"),
    ],
)
def test_partial_metadata_with_successful_fiscal_load_can_build(
    tmp_path: Path,
    inputs: dict[str, Any],
    stages: tuple[Mock, Mock, Mock],
    fiscal_outcome: str,
    fiscal_status: str,
) -> None:
    loaded = metadata_result(
        stages[0].return_value,
        tmp_path,
        status="PARTIAL",
        fiscal_outcome=fiscal_outcome,
        fiscal_status=fiscal_status,
    )
    stages[1].return_value = loaded
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "PARTIAL"
    assert result.company_run is stages[0].return_value
    assert result.metadata_result is loaded and result.metadata_status == "PARTIAL"
    assert (
        result.dbt_status == "COMPLETE" and result.dbt_result is stages[2].return_value
    )
    stages[2].assert_called_once()


def test_failed_returned_dbt_result_keeps_internal_diagnostics(
    tmp_path: Path, inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock]
) -> None:
    built = dbt_result(tmp_path, "FAILED")
    stages[2].return_value = built
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "PARTIAL" and result.dbt_status == "FAILED"
    assert result.company_run is stages[0].return_value
    assert result.metadata_result is stages[1].return_value
    assert result.dbt_result is built
    assert result.dbt_artifact_directory == built.artifact_directory
    assert result.dbt_error is result.dbt_skip_reason is None
    assert built.error_message == "dbt failure details"


def test_company_exception_retains_error_and_skips_downstream(
    inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock]
) -> None:
    stages[0].side_effect = CompanyRunError("evidence could not be finalized")
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "FAILED" and result.company_run is None
    assert result.company_error == "evidence could not be finalized"
    assert_downstream_skipped(result, stages)


def test_metadata_exception_preserves_company_and_skips_dbt(
    inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock]
) -> None:
    stages[1].side_effect = CompanyMetadataLoadError("metadata run identity mismatch")
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "PARTIAL" and result.company_run is stages[0].return_value
    assert result.company_error is None
    assert result.metadata_status == "FAILED" and result.metadata_result is None
    assert result.metadata_error == "metadata run identity mismatch"
    assert result.metadata_skip_reason is None
    assert result.dbt_status == "SKIPPED" and result.dbt_skip_reason
    assert (
        result.dbt_result is result.dbt_error is result.dbt_artifact_directory is None
    )
    stages[2].assert_not_called()


@pytest.mark.parametrize("created_artifacts", [False, True])
def test_dbt_exception_preserves_earlier_results_and_available_artifact_path(
    inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock], created_artifacts: bool
) -> None:
    directory = (
        inputs["dbt_artifact_directory"].resolve() if created_artifacts else None
    )
    stages[2].side_effect = DbtBuildError(
        "build launch failed", artifact_directory=directory
    )
    result = run_end_to_end_pipeline(**inputs)
    assert result.status == "PARTIAL"
    assert result.company_run is stages[0].return_value
    assert result.metadata_result is stages[1].return_value
    assert result.company_error is result.metadata_error is None
    assert result.dbt_status == "FAILED" and result.dbt_result is None
    assert result.dbt_error == "build launch failed" and result.dbt_skip_reason is None
    assert result.dbt_artifact_directory == directory
    stages[2].assert_called_once()


@pytest.mark.parametrize("stage_index", [0, 1, 2])
def test_unexpected_exceptions_propagate(
    inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock], stage_index: int
) -> None:
    error = RuntimeError("programming error")
    stages[stage_index].side_effect = error
    with pytest.raises(RuntimeError) as raised:
        run_end_to_end_pipeline(**inputs)
    assert raised.value is error
    for index, stage in enumerate(stages):
        assert stage.call_count == (1 if index <= stage_index else 0)


def filesystem_snapshot(root: Path) -> dict[str, bytes | str]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        if path.is_file()
        else "directory"
        for path in root.rglob("*")
        if not path.is_symlink()
    }


@pytest.mark.parametrize(
    "argument",
    [
        "bronze_directory",
        "silver_directory",
        "database_path",
        "run_directory",
        "dbt_project_directory",
        "dbt_profiles_directory",
        "dbt_artifact_directory",
    ],
)
def test_incorrect_path_types_fail_preflight_without_mutation(
    tmp_path: Path,
    inputs: dict[str, Any],
    stages: tuple[Mock, Mock, Mock],
    argument: str,
) -> None:
    before = filesystem_snapshot(tmp_path)
    inputs[argument] = str(inputs[argument])
    with pytest.raises(EndToEndPipelineError):
        run_end_to_end_pipeline(**inputs)
    assert filesystem_snapshot(tmp_path) == before
    for stage in stages:
        stage.assert_not_called()


@pytest.mark.parametrize(
    "timeout", [True, False, None, "600", 0, -1, float("inf"), float("nan"), 10**400]
)
def test_invalid_timeout_fails_preflight_without_mutation(
    tmp_path: Path,
    inputs: dict[str, Any],
    stages: tuple[Mock, Mock, Mock],
    timeout: object,
) -> None:
    before = filesystem_snapshot(tmp_path)
    inputs["dbt_timeout_seconds"] = timeout
    with pytest.raises(EndToEndPipelineError, match="finite positive"):
        run_end_to_end_pipeline(**inputs)
    assert filesystem_snapshot(tmp_path) == before
    for stage in stages:
        stage.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    [
        "project_missing",
        "profiles_missing",
        "project_config_missing",
        "profile_config_missing",
        "project_file",
        "artifact_exists",
        "artifact_file",
        "artifact_symlink",
        "parent_missing",
        "parent_file",
    ],
)
def test_invalid_dbt_paths_fail_preflight_without_mutation(
    tmp_path: Path,
    inputs: dict[str, Any],
    stages: tuple[Mock, Mock, Mock],
    invalid: str,
) -> None:
    directory = inputs["dbt_artifact_directory"]
    if invalid == "project_missing":
        inputs["dbt_project_directory"] = tmp_path / "missing"
    elif invalid == "profiles_missing":
        inputs["dbt_profiles_directory"] = tmp_path / "missing"
    elif invalid == "project_config_missing":
        (inputs["dbt_project_directory"] / "dbt_project.yml").unlink()
    elif invalid == "profile_config_missing":
        (inputs["dbt_profiles_directory"] / "profiles.yml").unlink()
    elif invalid == "project_file":
        inputs["dbt_project_directory"] = (
            inputs["dbt_profiles_directory"] / "profiles.yml"
        )
    elif invalid == "artifact_exists":
        directory.mkdir()
        (directory / "preserved").write_bytes(b"prior evidence")
    elif invalid == "artifact_file":
        directory.write_bytes(b"prior evidence")
    elif invalid == "artifact_symlink":
        directory.symlink_to(tmp_path / "absent")
    elif invalid == "parent_missing":
        inputs["dbt_artifact_directory"] = tmp_path / "missing" / "artifacts"
    elif invalid == "parent_file":
        inputs["dbt_artifact_directory"] = (
            inputs["dbt_profiles_directory"] / "profiles.yml" / "artifacts"
        )
    before = filesystem_snapshot(tmp_path)
    with pytest.raises(EndToEndPipelineError):
        run_end_to_end_pipeline(**inputs)
    assert filesystem_snapshot(tmp_path) == before
    if invalid == "artifact_symlink":
        assert directory.is_symlink() and not directory.exists()
    for stage in stages:
        stage.assert_not_called()


def test_system_aliases_are_accepted_without_resolving_managed_company_roots(
    tmp_path: Path, inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock]
) -> None:
    alias = tmp_path / "system alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    for name in (
        "dbt_project_directory",
        "dbt_profiles_directory",
        "dbt_artifact_directory",
    ):
        inputs[name] = alias / inputs[name].name
    inputs["database_path"] = alias / "catalog.duckdb"
    result = run_end_to_end_pipeline(**inputs)
    assert result.database_path == (tmp_path / "catalog.duckdb").resolve()
    assert result.status == "COMPLETE"
    assert stages[0].call_args.kwargs["bronze_directory"] is inputs["bronze_directory"]
    assert stages[0].call_args.kwargs["silver_directory"] is inputs["silver_directory"]
    assert stages[0].call_args.kwargs["run_directory"] is inputs["run_directory"]
    assert (
        stages[2].call_args.kwargs["artifact_directory"]
        == tmp_path.resolve() / "fresh dbt artifacts"
    )


def test_result_is_frozen_and_slotted(
    inputs: dict[str, Any], stages: tuple[Mock, Mock, Mock]
) -> None:
    result = run_end_to_end_pipeline(**inputs)
    assert isinstance(result, EndToEndPipelineResult)
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.status = "FAILED"  # type: ignore[misc]
