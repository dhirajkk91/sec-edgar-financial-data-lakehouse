"""The company-run record explains what happened and which SEC response was used.

This wraps company processing and catalog refresh, then saves the result
and original response together. If saving fails, earlier published data
is left in place rather than treated as if the work never happened.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from sec_edgar_lakehouse.bronze.company_filings import (
    CompanyFilingsDiscoveryError,
    _validate_selection_inputs,
)
from sec_edgar_lakehouse.filing_reference import _normalize_cik
from sec_edgar_lakehouse.pipeline.company_pipeline import (
    CompanyPipelineError,
    CompanyPipelineResult,
    run_company_pipeline,
)
from sec_edgar_lakehouse.pipeline.company_processing import CompanyProcessingError


class CompanyRunError(Exception):
    """Run identity, destination, or record persistence failed."""


@dataclass(frozen=True, slots=True)
class CompanyRunResult:
    """One run's status and final record path; a rerun does not change filing identity."""

    run_id: str
    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    pipeline_result: CompanyPipelineResult | None
    run_record_path: Path
    error_stage: str | None
    error_message: str | None
    terminal_sec_block: bool = False


def _available(path: Path) -> None:
    """Every run needs a new record path; a rerun cannot replace an earlier completed record."""
    if path.exists() or path.is_symlink():
        raise CompanyRunError(f"Run record already exists: {path}")


def _prepare(root: Path, cik: str) -> Path:
    """Create real run/CIK folders; OS path aliases above the managed root are allowed."""
    if not isinstance(root, Path):
        raise CompanyRunError("Run directory must be a pathlib.Path value")
    # Ancestors outside the managed root may be OS aliases (e.g. macOS /tmp).
    if root.is_symlink():
        raise CompanyRunError(f"Managed run directory is a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    company = root / f"cik={cik}"
    if company.is_symlink():
        raise CompanyRunError(f"Managed company directory is a symlink: {company}")
    company.mkdir(exist_ok=True)
    return company


def _write_file(path: Path, content: bytes) -> None:
    """Flush a new file beside its destination; clean up only this attempt's temp file."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _available(path)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _verify_evidence(staging: Path, record: dict[str, Any]) -> None:
    """Read the response and run JSON back before publishing their folder."""
    evidence = record["submissions_evidence"]
    expected = {"run.json"}
    if evidence is not None:
        expected.add("submissions.json")
    history = record.get("history_evidence", [])
    if history:
        expected.add("history")
    entries = list(staging.iterdir())
    if {item.name for item in entries} != expected or any(
        item.is_symlink() for item in entries
    ):
        raise CompanyRunError("Unexpected staged run evidence paths")
    if any(not (staging / name).is_file() for name in expected - {"history"}):
        raise CompanyRunError("Unexpected staged run evidence paths")
    if evidence is not None:
        content = (staging / "submissions.json").read_bytes()
        if (
            len(content) != evidence["size_bytes"]
            or hashlib.sha256(content).hexdigest() != evidence["sha256"]
        ):
            raise CompanyRunError("Submissions evidence verification failed")
    if history:
        directory = staging / "history"
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or {item.name for item in directory.iterdir()}
            != {item["name"] for item in history}
        ):
            raise CompanyRunError("Unexpected staged history evidence paths")
        for item in history:
            path = directory / item["name"]
            if path.is_symlink() or not path.is_file():
                raise CompanyRunError("History evidence must be a regular file")
            content = path.read_bytes()
            if (
                len(content) != item["size_bytes"]
                or hashlib.sha256(content).hexdigest() != item["sha256"]
            ):
                raise CompanyRunError("History evidence verification failed")
    with (staging / "run.json").open(encoding="utf-8") as stream:
        stored = json.load(stream)
    if stored != record:
        raise CompanyRunError("Run record verification failed")


def _publish_evidence(
    destination: Path, record: dict[str, Any], result: CompanyPipelineResult | None
) -> None:
    """Publish the run record and any source response together from staging."""
    _available(destination)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        if result is not None:
            discovery = result.processing.discovery
            content = discovery.submissions_content
            record["submissions_evidence"] = {
                "path": "submissions.json",
                "url": discovery.submissions_url,
                "retrieved_at": discovery.retrieved_at.astimezone(UTC).isoformat(),
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            _write_file(staging / "submissions.json", content)
            if discovery.history_listings:
                history_directory = staging / "history"
                history_directory.mkdir()
                record["history_evidence"] = []
                for listing in discovery.history_listings:
                    history_content = listing.content
                    name = listing.reference.name
                    record["history_evidence"].append(
                        {
                            "name": name,
                            "path": f"history/{name}",
                            "url": listing.url,
                            "retrieved_at": listing.retrieved_at.astimezone(
                                UTC
                            ).isoformat(),
                            "size_bytes": len(history_content),
                            "sha256": hashlib.sha256(history_content).hexdigest(),
                        }
                    )
                    _write_file(history_directory / name, history_content)
        _write_file(
            staging / "run.json",
            (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        # The run JSON and response bytes must agree before either becomes completed evidence.
        _verify_evidence(staging, record)
        _available(destination)
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _safe_error(message: str | None, user_agent: str) -> str | None:
    """Remove the configured User-Agent and email-like text from saved errors."""
    if message is None:
        return None
    if user_agent:
        message = message.replace(user_agent, "[redacted]")
    return re.sub(r"[^\s<>\"']+@[^\s<>\"']+", "[redacted]", message)


def _processing(
    result: CompanyPipelineResult | None, user_agent: str
) -> dict[str, Any] | None:
    """Save ordered filing results and counts, without copying financial facts."""
    if result is None:
        return None
    processing = result.processing
    return {
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
        "filing_results": [
            {
                "cik": item.filing.reference.cik,
                "accession_number": item.filing.reference.accession_number,
                "form": item.filing.form,
                "filing_date": item.filing.filing_date.isoformat(),
                "report_date": _iso_date(item.filing.report_date),
                "primary_document": item.filing.primary_document,
                "outcome": item.outcome,
                "bronze_status": item.bronze_status,
                "bronze_path": _path(item.bronze_path),
                "bronze_manifest_path": _path(item.bronze_manifest_path),
                "silver_status": item.silver_status,
                "silver_outcome": item.silver_outcome,
                "silver_version_path": _path(item.silver_version_path),
                "error_stage": item.error_stage,
                "error_message": _safe_error(item.error_message, user_agent),
            }
            for item in processing.filing_results
        ],
    }


def _catalog(result: CompanyPipelineResult | None, user_agent: str) -> dict[str, Any]:
    """Save refresh results; use null counts if there is no catalog result."""
    catalog = result.catalog_result if result else None
    return {
        "status": result.catalog_status if result else "SKIPPED",
        "database_path": _path(catalog.database_path) if catalog else None,
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
        "error": _safe_error(result.catalog_error, user_agent) if result else None,
    }


def _path(value: Path | None) -> str | None:
    """Missing stage paths stay JSON null rather than becoming the string None."""
    return str(value) if value is not None else None


def _iso_date(value: date | None) -> str | None:
    """Write optional selection dates in the same calendar-date form used by the CLI."""
    return value.isoformat() if value is not None else None


def execute_company_pipeline_run(
    cik: str,
    *,
    user_agent: str,
    forms: Collection[str],
    bronze_directory: Path,
    silver_directory: Path,
    database_path: Path,
    processing_run_id: str,
    run_directory: Path,
    filed_on_or_after: date | None = None,
    filed_on_or_before: date | None = None,
    limit: int | None = None,
    client: httpx.Client | None = None,
    request_interval_seconds: float = 0.5,
    include_history: bool = False,
    history_since: date | None = None,
    selected_accessions: Collection[str] | None = None,
) -> CompanyRunResult:
    """Run the company pipeline and save a new record with its source response.

    The run ID must be safe and unused. Expected pipeline or discovery errors
    can produce a FAILED record without response bytes. Unexpected errors
    raise. If saving the record fails, CompanyRunError is raised; Bronze,
    Silver and catalog outputs already published are left in place.
    """
    if (
        not isinstance(processing_run_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", processing_run_id) is None
    ):
        raise CompanyRunError(
            "Run ID must contain 1–128 ASCII letters, digits, hyphens or underscores"
        )
    try:
        normalized = _normalize_cik(cik)
        _validate_selection_inputs(forms, None, None, None)
        if any(
            boundary is not None and type(boundary) is not date
            for boundary in (filed_on_or_after, filed_on_or_before)
        ):
            raise CompanyRunError("Filing-date boundaries must be date values")
    except (TypeError, ValueError, CompanyFilingsDiscoveryError) as exc:
        raise CompanyRunError(str(exc)) from exc
    try:
        directory = _prepare(run_directory, normalized)
        path = directory / f"run_id={processing_run_id}"
        _available(path)
    except (TypeError, ValueError, OSError) as exc:
        raise CompanyRunError(str(exc)) from exc

    started_at = datetime.now(UTC)
    pipeline_result = None
    error_stage = error_message = None
    terminal_sec_block = False
    extra: dict[str, Any] = {}
    if include_history:
        extra["include_history"] = True
    if history_since is not None:
        extra["history_since"] = history_since
    if selected_accessions is not None:
        extra["selected_accessions"] = selected_accessions
    try:
        pipeline_result = run_company_pipeline(
            cik,
            user_agent=user_agent,
            forms=forms,
            bronze_directory=bronze_directory,
            silver_directory=silver_directory,
            database_path=database_path,
            processing_run_id=processing_run_id,
            filed_on_or_after=filed_on_or_after,
            filed_on_or_before=filed_on_or_before,
            limit=limit,
            client=client,
            request_interval_seconds=request_interval_seconds,
            **extra,
        )
    except (CompanyPipelineError, CompanyProcessingError) as exc:
        error_stage = (
            "PIPELINE" if isinstance(exc, CompanyPipelineError) else "PROCESSING"
        )
        error_message = _safe_error(str(exc), user_agent)
        terminal_sec_block = isinstance(exc, CompanyProcessingError) and exc.stop_run
    completed_at = datetime.now(UTC)
    status = pipeline_result.status if pipeline_result else "FAILED"
    if pipeline_result is not None:
        terminal_sec_block = pipeline_result.processing.terminal_sec_block
    record = {
        "schema_version": "1",
        "run_id": processing_run_id,
        "cik": normalized,
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "selection": {
            "forms": sorted(set(forms)),
            "filed_on_or_after": _iso_date(filed_on_or_after),
            "filed_on_or_before": _iso_date(filed_on_or_before),
            "limit": limit,
            **({"include_history": True} if include_history else {}),
            **(
                {"history_since": history_since.isoformat()}
                if history_since is not None
                else {}
            ),
            **(
                {"selected_accessions": sorted(selected_accessions)}
                if selected_accessions is not None
                else {}
            ),
        },
        "submissions_evidence": None,
        "processing": _processing(pipeline_result, user_agent),
        "catalog": _catalog(pipeline_result, user_agent),
        "error": {"stage": error_stage, "message": error_message}
        if error_stage
        else None,
        **({"terminal_sec_block": True} if terminal_sec_block else {}),
    }
    try:
        _prepare(run_directory, normalized)
        _publish_evidence(path, record, pipeline_result)
    except (OSError, TypeError, ValueError) as exc:
        raise CompanyRunError(f"Run record persistence failed: {exc}") from exc
    return CompanyRunResult(
        processing_run_id,
        status,
        pipeline_result,
        path / "run.json",
        error_stage,
        error_message,
        terminal_sec_block,
    )
