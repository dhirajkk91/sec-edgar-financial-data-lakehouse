"""Process selected filings for one company through Bronze and Silver."""

import math
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Literal

import httpx

from sec_edgar_lakehouse.company_filings import (
    CompanyFiling,
    CompanyFilingsDiscovery,
    CompanyFilingsDiscoveryError,
    _validate_selection_inputs,
    discover_company_filings,
    select_company_filings,
)
from sec_edgar_lakehouse.filing_discovery import DiscoveryError, _validate_user_agent
from sec_edgar_lakehouse.filing_ingestion import (
    _published_outcome,
    ingest_filing,
)
from sec_edgar_lakehouse.filing_inventory import InventoryError
from sec_edgar_lakehouse.filing_publication import PublicationError
from sec_edgar_lakehouse.filing_reference import _normalize_cik
from sec_edgar_lakehouse.filing_request import RequestPacer
from sec_edgar_lakehouse.silver_extraction import (
    SilverExtractionError,
    SilverInputError,
    extract_filing_facts,
)
from sec_edgar_lakehouse.silver_publication import (
    SilverPublicationError,
    publish_silver_extraction,
)

FilingOutcome = Literal["COMPLETE", "PARTIAL", "SKIPPED", "FAILED", "NOT_ATTEMPTED"]
ProcessingStatus = Literal["COMPLETE", "PARTIAL", "FAILED"]


class CompanyProcessingError(Exception):
    """Coordinator input or company discovery prevented a processable run."""


class _BronzeResolutionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CompanyFilingProcessResult:
    filing: CompanyFiling
    outcome: FilingOutcome
    bronze_status: str | None
    bronze_path: Path | None
    bronze_manifest_path: Path | None
    silver_status: str | None
    silver_outcome: str | None
    silver_version_path: Path | None
    error_stage: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class CompanyProcessingResult:
    processing_run_id: str
    discovery: CompanyFilingsDiscovery
    status: ProcessingStatus
    started_at: datetime
    completed_at: datetime
    filing_results: tuple[CompanyFilingProcessResult, ...]

    @property
    def selected_count(self) -> int:
        return len(self.filing_results)

    def _count(self, outcome: FilingOutcome) -> int:
        return sum(result.outcome == outcome for result in self.filing_results)

    @property
    def complete_count(self) -> int:
        return self._count("COMPLETE")

    @property
    def partial_count(self) -> int:
        return self._count("PARTIAL")

    @property
    def skipped_count(self) -> int:
        return self._count("SKIPPED")

    @property
    def failed_count(self) -> int:
        return self._count("FAILED")

    @property
    def not_attempted_count(self) -> int:
        return self._count("NOT_ATTEMPTED")

    @property
    def usable_count(self) -> int:
        return self.complete_count + self.partial_count + self.skipped_count


def process_company_filings(
    cik: str,
    *,
    user_agent: str,
    forms: Collection[str],
    bronze_directory: Path,
    silver_directory: Path,
    processing_run_id: str,
    filed_on_or_after: date | None = None,
    filed_on_or_before: date | None = None,
    limit: int | None = None,
    client: httpx.Client | None = None,
    request_interval_seconds: float = 0.5,
) -> CompanyProcessingResult:
    """Discover, select, and sequentially process one company's recent filings."""
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
    started_at = datetime.now(UTC)
    pacer = RequestPacer(
        float(request_interval_seconds), clock=monotonic, sleeper=sleep
    )
    if client is None:
        with httpx.Client() as owned_client:
            return _process_with_client(
                cik,
                user_agent,
                forms,
                bronze_directory,
                silver_directory,
                processing_run_id,
                filed_on_or_after,
                filed_on_or_before,
                limit,
                owned_client,
                pacer,
                started_at,
            )
    return _process_with_client(
        cik,
        user_agent,
        forms,
        bronze_directory,
        silver_directory,
        processing_run_id,
        filed_on_or_after,
        filed_on_or_before,
        limit,
        client,
        pacer,
        started_at,
    )


def _validate_inputs(
    cik: str,
    user_agent: str,
    forms: Collection[str],
    bronze_directory: Path,
    silver_directory: Path,
    processing_run_id: str,
    filed_on_or_after: date | None,
    filed_on_or_before: date | None,
    limit: int | None,
    request_interval_seconds: float,
) -> None:
    try:
        _normalize_cik(cik)
        _validate_user_agent(user_agent)
        _validate_selection_inputs(forms, filed_on_or_after, filed_on_or_before, limit)
        if not isinstance(bronze_directory, Path) or not isinstance(
            silver_directory, Path
        ):
            raise TypeError("Bronze and Silver directories must be pathlib.Path values")
        if (
            not isinstance(processing_run_id, str)
            or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", processing_run_id) is None
            or processing_run_id in {".", ".."}
        ):
            raise ValueError(
                "Processing run ID must be a safe non-empty path component"
            )
        if (
            isinstance(request_interval_seconds, bool)
            or not isinstance(request_interval_seconds, (int, float))
            or not math.isfinite(request_interval_seconds)
            or request_interval_seconds < 0.5
        ):
            raise ValueError(
                "Request interval must be a finite number of at least 0.5 seconds"
            )
    except (TypeError, ValueError, DiscoveryError, CompanyFilingsDiscoveryError) as exc:
        raise CompanyProcessingError(str(exc)) from exc


def _process_with_client(
    cik: str,
    user_agent: str,
    forms: Collection[str],
    bronze_directory: Path,
    silver_directory: Path,
    processing_run_id: str,
    filed_on_or_after: date | None,
    filed_on_or_before: date | None,
    limit: int | None,
    client: httpx.Client,
    pacer: RequestPacer,
    started_at: datetime,
) -> CompanyProcessingResult:
    try:
        discovery = discover_company_filings(
            cik, user_agent=user_agent, client=client, _pacer=pacer
        )
        selected = select_company_filings(
            discovery,
            forms=forms,
            filed_on_or_after=filed_on_or_after,
            filed_on_or_before=filed_on_or_before,
            limit=limit,
        )
    except CompanyFilingsDiscoveryError as exc:
        raise CompanyProcessingError(str(exc)) from exc

    results: list[CompanyFilingProcessResult] = []
    for index, filing in enumerate(selected):
        result, terminal = _process_filing(
            filing,
            user_agent,
            bronze_directory,
            silver_directory,
            processing_run_id,
            client,
            pacer,
        )
        results.append(result)
        if terminal:
            message = result.error_message or "SEC requested that the run stop"
            results.extend(
                _not_attempted(remaining, message)
                for remaining in selected[index + 1 :]
            )
            break
    status = _overall_status(results)
    return CompanyProcessingResult(
        processing_run_id,
        discovery,
        status,
        started_at,
        datetime.now(UTC),
        tuple(results),
    )


def _process_filing(
    filing: CompanyFiling,
    user_agent: str,
    bronze_directory: Path,
    silver_directory: Path,
    processing_run_id: str,
    client: httpx.Client,
    pacer: RequestPacer,
) -> tuple[CompanyFilingProcessResult, bool]:
    canonical = (
        bronze_directory.resolve()
        / f"cik={filing.reference.cik}"
        / f"accession={filing.reference.accession_number}"
    )
    try:
        existing = _existing_bronze(canonical)
    except _BronzeResolutionError as exc:
        return _failed(filing, "BRONZE_RESOLUTION", exc, bronze_path=canonical), False

    reused = existing is not None
    if existing is None:
        try:
            ingestion = ingest_filing(
                filing.reference,
                user_agent=user_agent,
                bronze_directory=bronze_directory,
                client=client,
                request_interval_seconds=pacer.interval,
                _pacer=pacer,
            )
        except (DiscoveryError, InventoryError, PublicationError) as exc:
            return _failed(filing, "BRONZE_INGESTION", exc), bool(
                isinstance(exc, DiscoveryError) and exc.stop_run
            )
        if ingestion.published is None:
            failure = ingestion.download_failure
            message = (
                failure.message if failure is not None else "Bronze ingestion failed"
            )
            return (
                CompanyFilingProcessResult(
                    filing,
                    "FAILED",
                    ingestion.status,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "BRONZE_INGESTION",
                    message,
                ),
                bool(failure is not None and failure.stop_run),
            )
        bronze_path = ingestion.published.path
        manifest_path = ingestion.published.manifest_path
    else:
        bronze_path, manifest_path = existing

    try:
        extraction = extract_filing_facts(bronze_path, manifest_path)
    except (SilverInputError, SilverExtractionError) as exc:
        return _failed(
            filing,
            "SILVER_EXTRACTION",
            exc,
            bronze_path=bronze_path,
            manifest_path=manifest_path,
        ), False

    try:
        bronze_status, _ = _published_outcome(manifest_path)
    except PublicationError as exc:
        return _failed(
            filing,
            "BRONZE_RESOLUTION",
            exc,
            bronze_path=bronze_path,
            manifest_path=manifest_path,
        ), False

    try:
        silver = publish_silver_extraction(
            extraction,
            bronze_manifest_path=manifest_path,
            silver_directory=silver_directory,
            processing_run_id=processing_run_id,
        )
    except SilverPublicationError as exc:
        return (
            CompanyFilingProcessResult(
                filing,
                "FAILED",
                bronze_status,
                bronze_path,
                manifest_path,
                extraction.status,
                None,
                exc.version_path,
                "SILVER_PUBLICATION",
                str(exc),
            ),
            False,
        )
    if not silver.active:
        return (
            CompanyFilingProcessResult(
                filing,
                "FAILED",
                bronze_status,
                bronze_path,
                manifest_path,
                extraction.status,
                silver.outcome,
                silver.version_path,
                "SILVER_PUBLICATION",
                "Equivalent Silver version exists but is not active",
            ),
            False,
        )

    if bronze_status == "PARTIAL" or extraction.status == "PARTIAL":
        outcome: FilingOutcome = "PARTIAL"
    elif reused and silver.outcome == "SKIPPED":
        outcome = "SKIPPED"
    else:
        outcome = "COMPLETE"
    return (
        CompanyFilingProcessResult(
            filing,
            outcome,
            bronze_status,
            bronze_path,
            manifest_path,
            extraction.status,
            silver.outcome,
            silver.version_path,
            None,
            None,
        ),
        False,
    )


def _existing_bronze(filing_path: Path) -> tuple[Path, Path] | None:
    cik_directory = filing_path.parent
    if cik_directory.is_symlink():
        raise _BronzeResolutionError(
            f"Canonical Bronze CIK path is a symlink: {cik_directory}"
        )
    if not filing_path.exists() and not filing_path.is_symlink():
        return None
    if filing_path.is_symlink() or not filing_path.is_dir():
        raise _BronzeResolutionError(
            f"Canonical Bronze filing must be a real directory: {filing_path}"
        )
    manifests = filing_path / "manifests"
    if manifests.is_symlink() or not manifests.is_dir():
        raise _BronzeResolutionError(
            f"Bronze manifests must be a real directory: {manifests}"
        )
    candidates = sorted(manifests.glob("run_id=*.json"))
    if len(candidates) != 1:
        raise _BronzeResolutionError(
            f"Expected exactly one Bronze run manifest, found {len(candidates)}"
        )
    manifest = candidates[0]
    if manifest.is_symlink() or not manifest.is_file():
        raise _BronzeResolutionError(
            f"Bronze manifest must be a regular non-symlink file: {manifest}"
        )
    return filing_path, manifest


def _failed(
    filing: CompanyFiling,
    stage: str,
    error: Exception,
    *,
    bronze_path: Path | None = None,
    manifest_path: Path | None = None,
) -> CompanyFilingProcessResult:
    return CompanyFilingProcessResult(
        filing,
        "FAILED",
        None,
        bronze_path,
        manifest_path,
        None,
        None,
        None,
        stage,
        str(error),
    )


def _not_attempted(filing: CompanyFiling, message: str) -> CompanyFilingProcessResult:
    return CompanyFilingProcessResult(
        filing,
        "NOT_ATTEMPTED",
        None,
        None,
        None,
        None,
        None,
        None,
        "NOT_ATTEMPTED",
        message,
    )


def _overall_status(results: list[CompanyFilingProcessResult]) -> ProcessingStatus:
    if not results or all(
        result.outcome in {"COMPLETE", "SKIPPED"} for result in results
    ):
        return "COMPLETE"
    usable = any(
        result.outcome in {"COMPLETE", "PARTIAL", "SKIPPED"} for result in results
    )
    return "PARTIAL" if usable else "FAILED"
