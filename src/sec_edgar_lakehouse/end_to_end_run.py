"""Execute once and publish a finalized end-to-end run summary."""

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from sec_edgar_lakehouse.company_metadata import CompanyFiscalMetadataResult
from sec_edgar_lakehouse.company_processing import (
    CompanyProcessingError,
    _validate_inputs,
)
from sec_edgar_lakehouse.company_run import _iso_date, _path, _safe_error
from sec_edgar_lakehouse.end_to_end_pipeline import (
    EndToEndPipelineError,
    EndToEndPipelineResult,
    run_end_to_end_pipeline,
)
from sec_edgar_lakehouse.filing_reference import _normalize_cik


class EndToEndRunError(Exception):
    """Invalid wrapper inputs or failed summary persistence; outputs remain retained."""

    def __init__(
        self,
        message: str,
        *,
        pipeline_result: EndToEndPipelineResult | None = None,
        run_record_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.pipeline_result = pipeline_result
        self.run_record_path = run_record_path


@dataclass(frozen=True, slots=True)
class EndToEndRunResult:
    run_id: str
    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    pipeline_result: EndToEndPipelineResult | None
    run_record_path: Path
    error_stage: str | None
    error_message: str | None


def _available(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise OSError(f"Final run record already exists: {path}")


def _prepare(root: Path, cik: str) -> Path:
    # OS aliases above the caller's managed root (e.g. /tmp) are permitted.
    if root.is_symlink():
        raise OSError(f"Managed final-run root is a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    company = root / f"cik={cik}"
    if company.is_symlink():
        raise OSError(f"Managed final-run CIK directory is a symlink: {company}")
    company.mkdir(exist_ok=True)
    return company


def _publish(path: Path, record: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        _available(path)
        content = (
            json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        stored = json.loads(temporary.read_text(encoding="utf-8"))
        if stored != record:
            raise ValueError("Final run record verification failed")
        _available(path)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _company(result: EndToEndPipelineResult, user_agent: str) -> dict[str, Any]:
    run = result.company_run
    pipeline = run.pipeline_result if run else None
    processing = pipeline.processing if pipeline else None
    catalog = pipeline.catalog_result if pipeline else None
    sha256 = None
    if run is not None:
        path = run.run_record_path
        if path.is_symlink() or not path.is_file():
            raise OSError(f"Company record must be a regular non-symlink file: {path}")
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "status": run.status if run else "FAILED" if result.company_error else None,
        "error": _safe_error(result.company_error, user_agent),
        "run_record_path": _path(run.run_record_path) if run else None,
        "run_record_sha256": sha256,
        "error_stage": run.error_stage if run else None,
        "error_message": _safe_error(run.error_message, user_agent) if run else None,
        "processing": {
            "status": processing.status,
            "company_name": processing.discovery.entity_name,
            **{
                name: getattr(processing, name)
                for name in (
                    "selected_count",
                    "complete_count",
                    "partial_count",
                    "skipped_count",
                    "failed_count",
                    "not_attempted_count",
                    "usable_count",
                )
            },
        }
        if processing
        else None,
        "catalog": {
            "status": pipeline.catalog_status,
            "error": _safe_error(pipeline.catalog_error, user_agent),
            **{
                name: getattr(catalog, name) if catalog else None
                for name in (
                    "active_filing_count",
                    "failure_count",
                    "facts_count",
                    "fact_dimensions_count",
                    "rejected_facts_count",
                )
            },
        }
        if pipeline
        else None,
    }


def _fiscal(item: CompanyFiscalMetadataResult, user_agent: str) -> dict[str, Any]:
    loaded = item.load_result
    metadata = loaded.metadata if loaded else None
    return {
        "cik": item.reference.cik,
        "accession_number": item.reference.accession_number,
        "outcome": item.outcome,
        "status": item.status,
        "error_stage": item.error_stage,
        "error_message": _safe_error(item.error_message, user_agent),
        "load_result": {
            **{
                name: getattr(metadata, name)
                for name in (
                    "selected_document_name",
                    "selected_document_sha256",
                    "extraction_version",
                    "metadata_run_id",
                    "submissions_sha256",
                    "fiscal_year_focus",
                    "fiscal_period_focus",
                )
            },
            "document_period_end_date": _iso_date(metadata.document_period_end_date),
            "issues": [
                {
                    "field_name": issue.field_name,
                    "reason_code": issue.reason_code,
                    "message": _safe_error(issue.message, user_agent),
                    "source_occurrence_ids": list(issue.source_occurrence_ids),
                }
                for issue in metadata.issues
            ],
        }
        if metadata
        else None,
    }


def _metadata(result: EndToEndPipelineResult, user_agent: str) -> dict[str, Any]:
    metadata = result.metadata_result
    filing = metadata.filing_metadata_result if metadata else None
    return {
        "status": result.metadata_status,
        "error": _safe_error(result.metadata_error, user_agent),
        "skip_reason": _safe_error(result.metadata_skip_reason, user_agent),
        "filing_metadata_error": _safe_error(metadata.filing_metadata_error, user_agent)
        if metadata
        else None,
        "filing_metadata": {
            **{
                name: getattr(filing, name)
                for name in (
                    "status",
                    "source_run_id",
                    "active_filing_count",
                    "inserted_count",
                    "already_existing_count",
                    "missing_count",
                    "conflict_count",
                )
            },
            "issues": [
                {
                    "cik": issue.cik,
                    "accession_number": issue.accession_number,
                    "reason_code": issue.reason_code,
                    "message": _safe_error(issue.message, user_agent),
                }
                for issue in filing.issues
            ],
        }
        if filing
        else None,
        "fiscal_results": [
            _fiscal(item, user_agent) for item in metadata.fiscal_results
        ]
        if metadata
        else None,
    }


def _dbt(
    result: EndToEndPipelineResult,
    project: Path,
    profiles: Path,
    artifacts: Path,
    user_agent: str,
) -> dict[str, Any]:
    build = result.dbt_result
    return {
        "status": result.dbt_status,
        "error": _safe_error(result.dbt_error, user_agent),
        "skip_reason": _safe_error(result.dbt_skip_reason, user_agent),
        "project_directory": str(project),
        "profiles_directory": str(profiles),
        "requested_artifact_directory": str(artifacts),
        "artifact_directory": _path(result.dbt_artifact_directory),
        "started_at": build.started_at.astimezone(UTC).isoformat() if build else None,
        "completed_at": build.completed_at.astimezone(UTC).isoformat()
        if build
        else None,
        "return_code": build.return_code if build else None,
        "invocation_id": build.invocation_id if build else None,
        "node_status_counts": [list(item) for item in build.node_status_counts]
        if build
        else None,
        "build_error": _safe_error(build.error_message, user_agent) if build else None,
        **{
            name: _path(getattr(build, name)) if build else None
            for name in (
                "stdout_path",
                "stderr_path",
                "manifest_path",
                "run_results_path",
            )
        },
    }


def execute_end_to_end_pipeline_run(
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
    end_to_end_run_directory: Path,
    filed_on_or_after: date | None = None,
    filed_on_or_before: date | None = None,
    limit: int | None = None,
    client: httpx.Client | None = None,
    request_interval_seconds: float = 0.5,
    dbt_timeout_seconds: float = 600.0,
) -> EndToEndRunResult:
    """Call the coordinator once, then atomically publish its final summary.

    Persistence failures retain the coordinator result and intended destination.
    Earlier company evidence, metadata and dbt outputs are never rewritten here.
    """
    path: Path | None = None
    try:
        normalized = _normalize_cik(cik)
        if (
            not isinstance(processing_run_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", processing_run_id) is None
        ):
            raise ValueError(
                "Run ID must contain 1–128 ASCII letters, digits, hyphens or underscores"
            )
        for name, value in (
            ("Bronze directory", bronze_directory),
            ("Silver directory", silver_directory),
            ("Database", database_path),
            ("Run directory", run_directory),
            ("dbt project directory", dbt_project_directory),
            ("dbt profiles directory", dbt_profiles_directory),
            ("dbt artifact directory", dbt_artifact_directory),
            ("Final-run directory", end_to_end_run_directory),
        ):
            if not isinstance(value, Path):
                raise TypeError(f"{name} must be a pathlib.Path value")
        _validate_inputs(
            cik,
            user_agent,
            forms,
            bronze_directory,
            silver_directory,
            processing_run_id,
            filed_on_or_after,
            filed_on_or_before,
            limit,
            request_interval_seconds,
        )
        if (
            isinstance(dbt_timeout_seconds, bool)
            or not isinstance(dbt_timeout_seconds, (int, float))
            or not math.isfinite(dbt_timeout_seconds)
            or dbt_timeout_seconds <= 0
        ):
            raise ValueError("dbt timeout must be a finite positive number")
        # Resolve summary configuration without requiring a first-run database
        # or preventing the coordinator from reporting its own preflight failure.
        database = database_path.resolve()
        project = dbt_project_directory.resolve()
        profiles = dbt_profiles_directory.resolve()
        artifacts = dbt_artifact_directory.resolve()
        selection = {
            "forms": sorted(set(forms)),
            "filed_on_or_after": _iso_date(filed_on_or_after),
            "filed_on_or_before": _iso_date(filed_on_or_before),
            "limit": limit,
            "request_interval_seconds": request_interval_seconds,
            "dbt_timeout_seconds": dbt_timeout_seconds,
        }
        json.dumps(selection, allow_nan=False)
        path = (
            end_to_end_run_directory
            / f"cik={normalized}"
            / f"run_id={processing_run_id}.json"
        )
        _available(path)
        _prepare(end_to_end_run_directory, normalized)
    except (
        OSError,
        TypeError,
        ValueError,
        OverflowError,
        CompanyProcessingError,
    ) as exc:
        raise EndToEndRunError(str(exc), run_record_path=path) from exc
    started_at = datetime.now(UTC)
    result = None
    error_stage = error_message = None
    try:
        result = run_end_to_end_pipeline(
            cik,
            user_agent=user_agent,
            forms=forms,
            bronze_directory=bronze_directory,
            silver_directory=silver_directory,
            database_path=database_path,
            processing_run_id=processing_run_id,
            run_directory=run_directory,
            dbt_project_directory=dbt_project_directory,
            dbt_profiles_directory=dbt_profiles_directory,
            dbt_artifact_directory=dbt_artifact_directory,
            filed_on_or_after=filed_on_or_after,
            filed_on_or_before=filed_on_or_before,
            limit=limit,
            client=client,
            request_interval_seconds=request_interval_seconds,
            dbt_timeout_seconds=dbt_timeout_seconds,
        )
    except EndToEndPipelineError as exc:
        error_stage = "PREFLIGHT"
        error_message = _safe_error(str(exc), user_agent)
    completed_at = datetime.now(UTC)
    status = result.status if result else "FAILED"
    try:
        record = {
            "schema_version": "1",
            "run_id": processing_run_id,
            "cik": normalized,
            "status": status,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "selection": selection,
            "database_path": str(result.database_path if result else database),
            "company": _company(result, user_agent) if result else None,
            "metadata": _metadata(result, user_agent) if result else None,
            "dbt": _dbt(result, project, profiles, artifacts, user_agent)
            if result
            else None,
            "error": {"stage": error_stage, "message": error_message}
            if error_stage
            else None,
        }
        _prepare(end_to_end_run_directory, normalized)
        _publish(path, record)
    except (OSError, TypeError, ValueError) as exc:
        raise EndToEndRunError(
            f"Final summary persistence failed: {_safe_error(str(exc), user_agent)}",
            pipeline_result=result,
            run_record_path=path,
        ) from exc
    return EndToEndRunResult(
        processing_run_id, status, result, path, error_stage, error_message
    )
