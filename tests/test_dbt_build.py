import builtins
import importlib
import json
import os
import runpy
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from sec_edgar_lakehouse import DbtBuildError, DbtBuildResult, run_dbt_build
from sec_edgar_lakehouse import dbt_build as build


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "local build with spaces"
    root.mkdir()
    database = root / "local catalog.duckdb"
    database.write_bytes(b"database verification stops at regular-file validation")
    project, profiles = root / "project files", root / "profile files"
    project.mkdir()
    profiles.mkdir()
    (project / "dbt_project.yml").write_text("name: fixture\n")
    (profiles / "profiles.yml").write_text("fixture: {}\n")
    return {
        "database_path": database,
        "project_directory": project,
        "profiles_directory": profiles,
        "artifact_directory": root / "fresh build",
    }


def artifacts(directory: Path, statuses: tuple[str, ...] = ("success", "pass")) -> None:
    target = directory / "target"
    target.mkdir()
    run_results = {
        "metadata": {"invocation_id": "fixture-invocation"},
        "args": {"which": "build"},
        "results": [
            {"unique_id": f"node.{index}", "status": status}
            for index, status in enumerate(statuses)
        ],
    }
    (target / "run_results.json").write_text(json.dumps(run_results))
    (target / "manifest.json").write_text(
        json.dumps({"metadata": {"invocation_id": "fixture-invocation"}})
    )


@pytest.fixture
def process(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(build, "_dbt_available", lambda: True)
    mocked = Mock()
    monkeypatch.setattr(build.subprocess, "run", mocked)
    return mocked


def simulate_build(
    process: Mock,
    inputs: dict[str, Any],
    *,
    statuses: tuple[str, ...] = ("success", "pass"),
    return_code: int = 0,
    write_artifacts: bool = True,
) -> None:
    def execute(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        kwargs["stdout"].write(b"stdout evidence\n")
        kwargs["stderr"].write(b"stderr warning alone is not failure\n")
        if write_artifacts:
            artifacts(inputs["artifact_directory"].resolve(), statuses)
        return subprocess.CompletedProcess(command, return_code)

    process.side_effect = execute


def test_exact_command_environment_paths_output_and_success(
    inputs: dict[str, Any], process: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEC_EDGAR_DUCKDB_PATH", "parent database")
    monkeypatch.setenv("SEC_USER_AGENT", "Private contact contact@example.org")
    parent_environment = os.environ.copy()
    simulate_build(
        process,
        inputs,
        statuses=("success", "pass", "warn", "no-op", "reused", "pass"),
    )
    result = run_dbt_build(**inputs)

    process.assert_called_once()
    command = process.call_args.args[0]
    kwargs = process.call_args.kwargs
    directory = inputs["artifact_directory"].resolve()
    assert command == [
        sys.executable,
        "-u",
        "-m",
        "dbt.cli.main",
        "build",
        "--project-dir",
        str(inputs["project_directory"].resolve()),
        "--profiles-dir",
        str(inputs["profiles_directory"].resolve()),
        "--target-path",
        str(directory / "target"),
        "--log-path",
        str(directory / "logs"),
        "--write-json",
    ]
    assert kwargs["shell"] is False and kwargs["check"] is False
    assert kwargs["cwd"] == inputs["project_directory"].resolve()
    assert kwargs["timeout"] == 600.0
    assert kwargs["env"] == {
        **parent_environment,
        "SEC_EDGAR_DUCKDB_PATH": str(inputs["database_path"].resolve()),
    }
    assert os.environ == parent_environment and kwargs["env"] is not os.environ
    assert kwargs["stdout"].name == str(directory / "stdout.log")
    assert kwargs["stderr"].name == str(directory / "stderr.log")
    assert kwargs["stdout"].closed and kwargs["stderr"].closed
    assert isinstance(result, DbtBuildResult)
    assert result.status == "COMPLETE" and result.return_code == 0
    assert result.database_path == inputs["database_path"].resolve()
    assert result.artifact_directory == directory
    assert result.stdout_path.read_bytes() == b"stdout evidence\n"
    assert result.stderr_path.read_bytes() == b"stderr warning alone is not failure\n"
    assert result.run_results_path == directory / "target/run_results.json"
    assert result.manifest_path == directory / "target/manifest.json"
    assert result.invocation_id == "fixture-invocation"
    assert result.node_status_counts == (
        ("no-op", 1),
        ("pass", 2),
        ("reused", 1),
        ("success", 1),
        ("warn", 1),
    )
    assert result.started_at.tzinfo is UTC and result.completed_at.tzinfo is UTC
    assert result.started_at <= result.completed_at
    assert result.error_message is None


def test_symlink_ancestor_aliases_and_custom_timeout_are_accepted(
    tmp_path: Path, inputs: dict[str, Any], process: Mock
) -> None:
    root = inputs["database_path"].parent
    alias = tmp_path / "system alias"
    alias.symlink_to(root, target_is_directory=True)
    inputs.update({key: alias / value.name for key, value in inputs.items()})
    simulate_build(process, inputs)
    result = run_dbt_build(**inputs, timeout_seconds=12)
    assert result.status == "COMPLETE"
    assert result.artifact_directory == root.resolve() / "fresh build"
    assert result.database_path == root.resolve() / "local catalog.duckdb"
    assert process.call_args.kwargs["timeout"] == 12


@pytest.mark.parametrize("write_artifacts", [True, False])
def test_nonzero_exit_retains_diagnostics_without_masking_failure(
    inputs: dict[str, Any], process: Mock, write_artifacts: bool
) -> None:
    simulate_build(
        process,
        inputs,
        return_code=2,
        statuses=("error", "skipped"),
        write_artifacts=write_artifacts,
    )
    result = run_dbt_build(**inputs)
    assert result.status == "FAILED" and result.return_code == 2
    assert result.error_message is not None and "return code 2" in result.error_message
    assert result.stdout_path.read_bytes() == b"stdout evidence\n"
    assert result.stderr_path.is_file()
    if write_artifacts:
        assert result.invocation_id == "fixture-invocation"
        assert result.run_results_path is not None and result.manifest_path is not None
        assert result.node_status_counts == (("error", 1), ("skipped", 1))
    else:
        assert result.run_results_path is result.manifest_path is None
        assert result.invocation_id is None and result.node_status_counts == ()
    process.assert_called_once()


def test_timeout_preserves_partial_output(
    inputs: dict[str, Any], process: Mock
) -> None:
    def timeout(command: list[str], **kwargs: Any) -> None:
        kwargs["stdout"].write(b"partial stdout")
        kwargs["stderr"].write(b"partial stderr")
        artifacts(inputs["artifact_directory"], ("success",))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    process.side_effect = timeout
    result = run_dbt_build(**inputs, timeout_seconds=0.25)
    assert result.status == "FAILED" and result.return_code is None
    assert (
        result.error_message is not None
        and "timed out after 0.25" in result.error_message
    )
    assert result.stdout_path.read_bytes() == b"partial stdout"
    assert result.stderr_path.read_bytes() == b"partial stderr"
    assert result.node_status_counts == (("success", 1),)
    assert result.artifact_directory.is_dir()
    process.assert_called_once()


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_run",
        "missing_manifest",
        "malformed",
        "non_object",
        "symlink",
        "mismatched_ids",
        "missing_id",
        "blank_id",
        "wrong_command",
        "empty_results",
        "duplicate_ids",
        "missing_node_id",
        "blank_node_status",
        "non_object_node",
    ],
)
def test_invalid_success_artifacts_return_failed(
    inputs: dict[str, Any], process: Mock, invalid: str
) -> None:
    simulate_build(process, inputs)
    execute = process.side_effect

    def corrupt(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        completed = execute(command, **kwargs)
        target = inputs["artifact_directory"] / "target"
        run_path, manifest_path = target / "run_results.json", target / "manifest.json"
        run = json.loads(run_path.read_text())
        if invalid in {"missing_run", "missing_manifest"}:
            (run_path if invalid == "missing_run" else manifest_path).unlink()
        elif invalid == "malformed":
            run_path.write_text("{")
        elif invalid == "non_object":
            manifest_path.write_text("[]")
        elif invalid == "symlink":
            saved = target / "other.json"
            run_path.rename(saved)
            run_path.symlink_to(saved)
        elif invalid == "mismatched_ids":
            manifest_path.write_text(
                json.dumps({"metadata": {"invocation_id": "other"}})
            )
        else:
            if invalid == "missing_id":
                run["metadata"] = {}
            elif invalid == "blank_id":
                run["metadata"]["invocation_id"] = " "
            elif invalid == "wrong_command":
                run["args"]["which"] = "run"
            elif invalid == "empty_results":
                run["results"] = []
            elif invalid == "duplicate_ids":
                run["results"][1]["unique_id"] = run["results"][0]["unique_id"]
            elif invalid == "missing_node_id":
                del run["results"][0]["unique_id"]
            elif invalid == "blank_node_status":
                run["results"][0]["status"] = " "
            elif invalid == "non_object_node":
                run["results"][0] = None
            run_path.write_text(json.dumps(run))
        return completed

    process.side_effect = corrupt
    result = run_dbt_build(**inputs)
    assert result.status == "FAILED" and result.return_code == 0
    assert result.error_message
    assert result.stdout_path.is_file() and result.stderr_path.is_file()
    process.assert_called_once()


@pytest.mark.parametrize(
    "status",
    ["error", "fail", "skipped", "partial success", "runtime error", "unknown"],
)
def test_unsuccessful_node_statuses_prevent_complete(
    inputs: dict[str, Any], process: Mock, status: str
) -> None:
    simulate_build(process, inputs, statuses=("success", status))
    result = run_dbt_build(**inputs)
    assert result.status == "FAILED" and result.return_code == 0
    assert result.node_status_counts == tuple(sorted((("success", 1), (status, 1))))
    assert result.error_message is not None and status in result.error_message


@pytest.mark.parametrize(
    "argument",
    ["database_path", "project_directory", "profiles_directory", "artifact_directory"],
)
def test_wrong_path_types_rejected_before_artifacts_or_process(
    inputs: dict[str, Any], process: Mock, argument: str
) -> None:
    directory = inputs["artifact_directory"]
    inputs[argument] = str(inputs[argument])
    with pytest.raises(DbtBuildError) as raised:
        run_dbt_build(**inputs)
    assert raised.value.artifact_directory is None
    assert not directory.exists()
    process.assert_not_called()


@pytest.mark.parametrize(
    "timeout", [True, False, None, "600", 0, -1, float("inf"), float("nan")]
)
def test_invalid_timeouts_rejected_before_execution(
    inputs: dict[str, Any], process: Mock, timeout: object
) -> None:
    with pytest.raises(DbtBuildError, match="finite positive"):
        run_dbt_build(**inputs, timeout_seconds=timeout)  # type: ignore[arg-type]
    assert not inputs["artifact_directory"].exists()
    process.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_database",
        "database_directory",
        "missing_project",
        "missing_profiles",
        "project_file",
        "missing_project_config",
        "missing_profile_config",
        "missing_parent",
        "parent_file",
        "existing_directory",
        "existing_file",
        "dangling_symlink",
    ],
)
def test_invalid_filesystem_inputs_never_execute_or_overwrite(
    inputs: dict[str, Any], process: Mock, invalid: str
) -> None:
    database, directory = inputs["database_path"], inputs["artifact_directory"]
    if invalid in {"missing_database", "database_directory"}:
        database.unlink()
        if invalid == "database_directory":
            database.mkdir()
    elif invalid in {"missing_project", "missing_profiles"}:
        inputs[
            "project_directory"
            if invalid == "missing_project"
            else "profiles_directory"
        ] = directory.parent / "missing"
    elif invalid == "project_file":
        inputs["project_directory"] = database
    elif invalid == "missing_project_config":
        (inputs["project_directory"] / "dbt_project.yml").unlink()
    elif invalid == "missing_profile_config":
        (inputs["profiles_directory"] / "profiles.yml").unlink()
    elif invalid == "missing_parent":
        inputs["artifact_directory"] = directory / "new"
    elif invalid == "parent_file":
        inputs["artifact_directory"] = database / "new"
    elif invalid == "existing_directory":
        directory.mkdir()
        (directory / "preserved").write_bytes(b"original")
    elif invalid == "existing_file":
        directory.write_bytes(b"original")
    elif invalid == "dangling_symlink":
        directory.symlink_to(directory.parent / "absent")
    with pytest.raises(DbtBuildError) as raised:
        run_dbt_build(**inputs)
    assert raised.value.artifact_directory is None
    process.assert_not_called()
    if invalid == "existing_directory":
        assert (directory / "preserved").read_bytes() == b"original"
    elif invalid == "existing_file":
        assert directory.read_bytes() == b"original"
    elif invalid == "dangling_symlink":
        assert directory.is_symlink() and not directory.exists()
    else:
        assert not directory.exists()


@pytest.mark.parametrize("missing_parent_package", [False, True])
def test_missing_optional_dbt_has_no_artifacts_or_process(
    inputs: dict[str, Any],
    process: Mock,
    monkeypatch: pytest.MonkeyPatch,
    missing_parent_package: bool,
) -> None:
    monkeypatch.setattr(build, "_dbt_available", ORIGINAL_DBT_AVAILABLE)
    finder = Mock(
        return_value=None,
        side_effect=ModuleNotFoundError("dbt") if missing_parent_package else None,
    )
    monkeypatch.setattr(build.importlib.util, "find_spec", finder)
    with pytest.raises(DbtBuildError, match="unavailable") as raised:
        run_dbt_build(**inputs)
    assert raised.value.artifact_directory is None
    finder.assert_called_once_with("dbt.cli.main")
    assert not inputs["artifact_directory"].exists()
    process.assert_not_called()


ORIGINAL_DBT_AVAILABLE = build._dbt_available


def test_module_and_package_import_without_dbt(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def without_dbt(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "dbt" or name.startswith("dbt."):
            raise ModuleNotFoundError("optional dbt is unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_dbt)
    assert build.__file__ is not None
    loaded = runpy.run_path(build.__file__)
    assert callable(loaded["run_dbt_build"])
    package = importlib.import_module("sec_edgar_lakehouse")
    assert callable(importlib.reload(package).run_dbt_build)


def test_launch_failure_carries_created_artifact_directory(
    inputs: dict[str, Any], process: Mock
) -> None:
    process.side_effect = OSError("cannot launch interpreter")
    with pytest.raises(DbtBuildError, match="process-launch") as raised:
        run_dbt_build(**inputs)
    assert raised.value.artifact_directory == inputs["artifact_directory"].resolve()
    assert (inputs["artifact_directory"] / "stdout.log").is_file()
    assert (inputs["artifact_directory"] / "stderr.log").is_file()
    process.assert_called_once()


def test_artifact_filesystem_failure_carries_attempt(
    inputs: dict[str, Any], process: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulate_build(process, inputs)
    monkeypatch.setattr(
        Path, "read_text", Mock(side_effect=PermissionError("cannot read artifacts"))
    )
    with pytest.raises(DbtBuildError) as raised:
        run_dbt_build(**inputs)
    assert raised.value.artifact_directory == inputs["artifact_directory"].resolve()
    assert inputs["artifact_directory"].is_dir()


def test_unexpected_programming_errors_propagate(
    inputs: dict[str, Any], process: Mock
) -> None:
    error = RuntimeError("programming failure")
    process.side_effect = error
    with pytest.raises(RuntimeError) as raised:
        run_dbt_build(**inputs)
    assert raised.value is error
    assert inputs["artifact_directory"].is_dir()
    process.assert_called_once()


def test_result_is_frozen_and_slotted(inputs: dict[str, Any], process: Mock) -> None:
    simulate_build(process, inputs)
    result = run_dbt_build(**inputs)
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.status = "FAILED"  # type: ignore[misc]
