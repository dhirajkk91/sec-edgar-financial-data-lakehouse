"""Connect one company run, metadata loading and a local dbt build."""

import math
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import httpx

from sec_edgar_lakehouse.company_metadata import (
    CompanyMetadataLoadError,
    CompanyMetadataLoadResult,
    load_company_run_metadata,
)
from sec_edgar_lakehouse.company_run import (
    CompanyRunError,
    CompanyRunResult,
    execute_company_pipeline_run,
)
from sec_edgar_lakehouse.dbt_build import DbtBuildError, DbtBuildResult, run_dbt_build


class EndToEndPipelineError(Exception):
    """Wrapper preflight prevented the company stage from starting."""


@dataclass(frozen=True, slots=True)
class EndToEndPipelineResult:
    """Retained stage outcomes; execution status does not imply financial quality."""

    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    database_path: Path
    company_run: CompanyRunResult | None
    company_error: str | None
    metadata_status: Literal["COMPLETE", "PARTIAL", "FAILED", "SKIPPED"]
    metadata_result: CompanyMetadataLoadResult | None
    metadata_error: str | None
    metadata_skip_reason: str | None
    dbt_status: Literal["COMPLETE", "FAILED", "SKIPPED"]
    dbt_result: DbtBuildResult | None
    dbt_error: str | None
    dbt_skip_reason: str | None
    dbt_artifact_directory: Path | None


def _preflight(
    bronze_directory: Path,
    silver_directory: Path,
    database_path: Path,
    run_directory: Path,
    project_directory: Path,
    profiles_directory: Path,
    artifact_directory: Path,
    timeout_seconds: float,
) -> tuple[Path, Path, Path, Path]:
    for name, path in (
        ("Bronze directory", bronze_directory),
        ("Silver directory", silver_directory),
        ("Database", database_path),
        ("Run directory", run_directory),
        ("dbt project directory", project_directory),
        ("dbt profiles directory", profiles_directory),
        ("dbt artifact directory", artifact_directory),
    ):
        if not isinstance(path, Path):
            raise EndToEndPipelineError(f"{name} must be a pathlib.Path value")
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds, (int, float)
    ):
        raise EndToEndPipelineError("dbt timeout must be a finite positive number")
    try:
        finite = math.isfinite(timeout_seconds)
    except OverflowError as exc:
        raise EndToEndPipelineError(
            "dbt timeout must be a finite positive number"
        ) from exc
    if not finite or timeout_seconds <= 0:
        raise EndToEndPipelineError("dbt timeout must be a finite positive number")
    try:
        if artifact_directory.exists() or artifact_directory.is_symlink():
            raise EndToEndPipelineError("dbt artifact directory must not already exist")
        # A first catalog refresh may create the database; no connection is opened.
        database = database_path.resolve()
        project = project_directory.resolve(strict=True)
        profiles = profiles_directory.resolve(strict=True)
        for directory, filename in (
            (project, "dbt_project.yml"),
            (profiles, "profiles.yml"),
        ):
            if not directory.is_dir() or not (directory / filename).is_file():
                raise EndToEndPipelineError(
                    f"Directory must exist and contain {filename}"
                )
        parent = artifact_directory.parent.resolve(strict=True)
        if not parent.is_dir():
            raise EndToEndPipelineError(
                "dbt artifact parent must be an existing directory"
            )
        return database, project, profiles, parent / artifact_directory.name
    except (OSError, ValueError) as exc:
        raise EndToEndPipelineError(f"Invalid pipeline paths: {exc}") from exc


def _skip_downstream(
    status: Literal["COMPLETE", "PARTIAL", "FAILED"],
    database: Path,
    reason: str,
    *,
    company_run: CompanyRunResult | None = None,
    company_error: str | None = None,
) -> EndToEndPipelineResult:
    return EndToEndPipelineResult(
        status=status,
        database_path=database,
        company_run=company_run,
        company_error=company_error,
        metadata_status="SKIPPED",
        metadata_result=None,
        metadata_error=None,
        metadata_skip_reason=reason,
        dbt_status="SKIPPED",
        dbt_result=None,
        dbt_error=None,
        dbt_skip_reason=reason,
        dbt_artifact_directory=None,
    )


def run_end_to_end_pipeline(
    cik: str,
    *,
    user_agent: str,
    forms: Collection[str],
    bronze_directory: Path,
    silver_directory: Path,
    database_path: Path,
    processing_run_id: str,
    run_directory: Path,
    dbt_project_directory: Path,
    dbt_profiles_directory: Path,
    dbt_artifact_directory: Path,
    filed_on_or_after: date | None = None,
    filed_on_or_before: date | None = None,
    limit: int | None = None,
    client: httpx.Client | None = None,
    request_interval_seconds: float = 0.5,
    dbt_timeout_seconds: float = 600.0,
) -> EndToEndPipelineResult:
    """Run each eligible stage once, preserving evidence and earlier partial outcomes.

    The existing APIs own data validation, publication and client/connection
    lifetimes. This coordinator does not retry, query Gold, rewrite run records
    or roll back outputs after a later failure.
    """
    database, project, profiles, artifacts = _preflight(
        bronze_directory,
        silver_directory,
        database_path,
        run_directory,
        dbt_project_directory,
        dbt_profiles_directory,
        dbt_artifact_directory,
        dbt_timeout_seconds,
    )
    try:
        company_run = execute_company_pipeline_run(
            cik,
            user_agent=user_agent,
            forms=forms,
            bronze_directory=bronze_directory,
            silver_directory=silver_directory,
            database_path=database,
            processing_run_id=processing_run_id,
            run_directory=run_directory,
            filed_on_or_after=filed_on_or_after,
            filed_on_or_before=filed_on_or_before,
            limit=limit,
            client=client,
            request_interval_seconds=request_interval_seconds,
        )
    except CompanyRunError as exc:
        return _skip_downstream(
            "FAILED",
            database,
            "Company stage raised an error; no run result is available",
            company_error=str(exc),
        )
    if company_run.status == "FAILED":
        return _skip_downstream(
            "FAILED", database, "Company run failed", company_run=company_run
        )
    pipeline = company_run.pipeline_result
    if pipeline is None:
        return _skip_downstream(
            "FAILED",
            database,
            "Company run has no pipeline result",
            company_run=company_run,
        )
    processing = pipeline.processing
    if company_run.status == "COMPLETE" and processing.selected_count == 0:
        return _skip_downstream(
            "COMPLETE",
            database,
            "Company run selected no filings",
            company_run=company_run,
        )
    if processing.usable_count == 0:
        return _skip_downstream(
            "FAILED",
            database,
            "Selected filings produced no usable Silver",
            company_run=company_run,
        )
    if (
        pipeline.catalog_status not in {"COMPLETE", "PARTIAL"}
        or pipeline.catalog_result is None
    ):
        return _skip_downstream(
            "PARTIAL",
            database,
            "Company run has no eligible catalog refresh result",
            company_run=company_run,
        )
    metadata_result = None
    metadata_error = None
    metadata_skip_reason = None
    dbt_skip_reason: str | None
    try:
        metadata_result = load_company_run_metadata(company_run, database_path=database)
    except CompanyMetadataLoadError as exc:
        metadata_status: Literal["COMPLETE", "PARTIAL", "FAILED", "SKIPPED"] = "FAILED"
        metadata_error = str(exc)
        dbt_skip_reason = "Metadata stage raised an error"
    else:
        metadata_status = metadata_result.status
        if metadata_status == "SKIPPED":
            metadata_skip_reason = (
                metadata_result.skip_reason or "Metadata loader skipped this run"
            )
        if metadata_status not in {"COMPLETE", "PARTIAL"}:
            dbt_skip_reason = (
                f"Metadata status {metadata_status} is not eligible for dbt"
            )
        elif not any(
            item.outcome in {"INSERTED", "ALREADY_EXISTS"}
            and item.status in {"COMPLETE", "PARTIAL"}
            for item in metadata_result.fiscal_results
        ):
            dbt_skip_reason = "Metadata loading produced no successful fiscal load"
        else:
            dbt_skip_reason = None
    dbt_result = None
    dbt_error = None
    dbt_artifact_path = None
    dbt_status: Literal["COMPLETE", "FAILED", "SKIPPED"] = "SKIPPED"
    if dbt_skip_reason is None:
        try:
            dbt_result = run_dbt_build(
                database_path=database,
                project_directory=project,
                profiles_directory=profiles,
                artifact_directory=artifacts,
                timeout_seconds=dbt_timeout_seconds,
            )
        except DbtBuildError as exc:
            dbt_status = "FAILED"
            dbt_error = str(exc)
            dbt_artifact_path = exc.artifact_directory
        else:
            dbt_status = dbt_result.status
            dbt_artifact_path = dbt_result.artifact_directory
    status: Literal["COMPLETE", "PARTIAL", "FAILED"] = (
        "COMPLETE"
        if company_run.status == metadata_status == dbt_status == "COMPLETE"
        else "PARTIAL"
    )
    return EndToEndPipelineResult(
        status=status,
        database_path=database,
        company_run=company_run,
        company_error=None,
        metadata_status=metadata_status,
        metadata_result=metadata_result,
        metadata_error=metadata_error,
        metadata_skip_reason=metadata_skip_reason,
        dbt_status=dbt_status,
        dbt_result=dbt_result,
        dbt_error=dbt_error,
        dbt_skip_reason=dbt_skip_reason,
        dbt_artifact_directory=dbt_artifact_path,
    )
