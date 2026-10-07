"""Execute one local dbt build and retain its invocation evidence."""

import importlib.util
import json
import math
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_SUCCESS_STATUSES = frozenset({"success", "pass", "warn", "no-op", "reused"})


class DbtBuildError(Exception):
    """Invalid input, unavailable dbt, launch failure or filesystem failure."""

    def __init__(self, message: str, *, artifact_directory: Path | None = None):
        super().__init__(message)
        self.artifact_directory = artifact_directory


@dataclass(frozen=True, slots=True)
class DbtBuildResult:
    """Build execution outcome, independent of the financial data's quality."""

    status: Literal["COMPLETE", "FAILED"]
    database_path: Path
    artifact_directory: Path
    started_at: datetime
    completed_at: datetime
    return_code: int | None
    invocation_id: str | None
    stdout_path: Path
    stderr_path: Path
    run_results_path: Path | None
    manifest_path: Path | None
    node_status_counts: tuple[tuple[str, int], ...]
    error_message: str | None


def _validate_inputs(
    database_path: Path,
    project_directory: Path,
    profiles_directory: Path,
    artifact_directory: Path,
    timeout_seconds: float,
) -> tuple[Path, Path, Path, Path]:
    for name, path in (
        ("Database", database_path),
        ("Project directory", project_directory),
        ("Profiles directory", profiles_directory),
        ("Artifact directory", artifact_directory),
    ):
        if not isinstance(path, Path):
            raise DbtBuildError(f"{name} must be a pathlib.Path value")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise DbtBuildError("Timeout must be a finite positive number")
    if artifact_directory.exists() or artifact_directory.is_symlink():
        raise DbtBuildError("Artifact directory must not already exist")
    database = database_path.resolve(strict=True)
    if not database.is_file():
        raise DbtBuildError("Database must be an existing regular file")
    project = project_directory.resolve(strict=True)
    profiles = profiles_directory.resolve(strict=True)
    for directory, filename in (
        (project, "dbt_project.yml"),
        (profiles, "profiles.yml"),
    ):
        if not directory.is_dir() or not (directory / filename).is_file():
            raise DbtBuildError(f"Directory must exist and contain {filename}")
    parent = artifact_directory.parent.resolve(strict=True)
    if not parent.is_dir():
        raise DbtBuildError("Artifact parent must be an existing directory")
    artifacts = parent / artifact_directory.name
    return database, project, profiles, artifacts


def _dbt_available() -> bool:
    # Finding this module may import dbt's parent packages, so do it only at call
    # time. Importing the lakehouse package never requires the optional group.
    try:
        return importlib.util.find_spec("dbt.cli.main") is not None
    except ModuleNotFoundError:
        return False


def _json_artifact(
    path: Path,
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    if path.is_symlink() or not path.is_file():
        return None, None, f"{path.name} must be a regular, non-symlink file"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return path, None, f"{path.name} is invalid JSON: {exc}"
    if not isinstance(value, dict):
        return path, None, f"{path.name} must contain a JSON object"
    return path, value, None


def _invocation_id(artifact: dict[str, Any] | None) -> str | None:
    metadata = artifact.get("metadata") if artifact is not None else None
    value = metadata.get("invocation_id") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def _node_counts(
    run_results: dict[str, Any],
) -> tuple[tuple[tuple[str, int], ...], str | None]:
    results = run_results.get("results")
    if not isinstance(results, list) or not results:
        return (), "run_results.json must contain at least one node"
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    error = None
    for node in results:
        if not isinstance(node, dict):
            error = error or "Every result node must be a JSON object"
            continue
        identity, status = node.get("unique_id"), node.get("status")
        if not isinstance(identity, str) or not identity.strip():
            error = error or "Every result node must have a non-empty unique ID"
        elif identity in seen:
            error = error or "Result node IDs must be unique"
        else:
            seen.add(identity)
        if not isinstance(status, str) or not status.strip():
            error = error or "Every result node must have a non-empty status"
        else:
            counts[status] += 1
            if status not in _SUCCESS_STATUSES:
                error = error or f"Unsuccessful or unrecognized node status: {status}"
    return tuple(sorted(counts.items())), error


@dataclass(frozen=True, slots=True)
class _Artifacts:
    run_results_path: Path | None
    manifest_path: Path | None
    invocation_id: str | None
    counts: tuple[tuple[str, int], ...]
    error: str | None


def _inspect_artifacts(directory: Path) -> _Artifacts:
    run_path, run, run_error = _json_artifact(directory / "target/run_results.json")
    manifest_path, manifest, manifest_error = _json_artifact(
        directory / "target/manifest.json"
    )
    errors = [error for error in (run_error, manifest_error) if error is not None]
    run_id, manifest_id = _invocation_id(run), _invocation_id(manifest)
    if run is not None and run_id is None:
        errors.append("run_results.json requires a non-empty invocation ID")
    if manifest is not None and manifest_id is None:
        errors.append("manifest.json requires a non-empty invocation ID")
    if run_id is not None and manifest_id is not None and run_id != manifest_id:
        errors.append("Artifact invocation IDs do not match")
    counts: tuple[tuple[str, int], ...] = ()
    if run is not None:
        args = run.get("args")
        if not isinstance(args, dict) or args.get("which") != "build":
            errors.append("run_results.json must identify the command as build")
        counts, node_error = _node_counts(run)
        if node_error is not None:
            errors.append(node_error)
    return _Artifacts(
        run_path,
        manifest_path,
        run_id or manifest_id,
        counts,
        "; ".join(errors) if errors else None,
    )


def run_dbt_build(
    *,
    database_path: Path,
    project_directory: Path,
    profiles_directory: Path,
    artifact_directory: Path,
    timeout_seconds: float = 600.0,
) -> DbtBuildResult:
    """Execute dbt once in the current environment, preserving all attempt evidence.

    A failed build can have changed Gold relations. This runner does not perform
    database backup, rollback or promotion, nor interpret financial quality.
    """
    created: Path | None = None
    try:
        database, project, profiles, artifacts = _validate_inputs(
            database_path,
            project_directory,
            profiles_directory,
            artifact_directory,
            timeout_seconds,
        )
        if not _dbt_available():
            raise DbtBuildError("dbt is unavailable in the current Python environment")
        artifacts.mkdir()
        created = artifacts
        stdout_path, stderr_path = artifacts / "stdout.log", artifacts / "stderr.log"
        command = [
            sys.executable,
            "-u",
            "-m",
            "dbt.cli.main",
            "build",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(profiles),
            "--target-path",
            str(artifacts / "target"),
            "--log-path",
            str(artifacts / "logs"),
            "--write-json",
        ]
        environment = os.environ.copy()
        environment["SEC_EDGAR_DUCKDB_PATH"] = str(database)
        started_at = datetime.now(UTC)
        error = None
        return_code: int | None = None
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            try:
                process = subprocess.run(
                    command,
                    shell=False,
                    cwd=project,
                    env=environment,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                error = f"dbt build timed out after {timeout_seconds} seconds"
            else:
                return_code = process.returncode
                if return_code != 0:
                    error = f"dbt build exited with return code {return_code}"
        completed_at = datetime.now(UTC)
        evidence = _inspect_artifacts(artifacts)
        if evidence.error is not None:
            error = (
                f"{error}; artifact diagnostics: {evidence.error}"
                if error is not None
                else evidence.error
            )
        return DbtBuildResult(
            "FAILED" if error is not None else "COMPLETE",
            database,
            artifacts,
            started_at,
            completed_at,
            return_code,
            evidence.invocation_id,
            stdout_path,
            stderr_path,
            evidence.run_results_path,
            evidence.manifest_path,
            evidence.counts,
            error,
        )
    except OSError as exc:
        raise DbtBuildError(
            f"dbt build filesystem or process-launch failure: {exc}",
            artifact_directory=created,
        ) from exc
