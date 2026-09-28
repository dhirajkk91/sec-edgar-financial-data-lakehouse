"""Coordinate company filing processing with one Silver catalog refresh."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import httpx

from sec_edgar_lakehouse.company_processing import (
    CompanyProcessingResult,
    process_company_filings,
)
from sec_edgar_lakehouse.silver_catalog import (
    SilverCatalogError,
    SilverCatalogResult,
    refresh_silver_catalog,
)

_PipelineStatus = Literal["COMPLETE", "PARTIAL", "FAILED"]
_CatalogStatus = Literal["COMPLETE", "PARTIAL", "SKIPPED", "FAILED"]


class CompanyPipelineError(Exception):
    """Wrapper input prevented the company pipeline from starting."""


@dataclass(frozen=True, slots=True)
class CompanyPipelineResult:
    """Company processing and its single post-processing catalog refresh."""

    status: _PipelineStatus
    processing: CompanyProcessingResult
    catalog_status: _CatalogStatus
    catalog_result: SilverCatalogResult | None
    catalog_error: str | None

    def __post_init__(self) -> None:
        if self.catalog_status in {"COMPLETE", "PARTIAL"}:
            if self.catalog_result is None or self.catalog_error is not None:
                raise ValueError(
                    "A completed catalog refresh requires a result and no error"
                )
            if self.catalog_result.status != self.catalog_status:
                raise ValueError("Catalog status must match the catalog result")
        elif self.catalog_status == "FAILED":
            if self.catalog_result is not None or self.catalog_error is None:
                raise ValueError("A failed catalog refresh requires only an error")
        elif self.catalog_status == "SKIPPED":
            if self.catalog_result is not None or self.catalog_error is not None:
                raise ValueError("A skipped catalog refresh has no result or error")
        else:
            raise ValueError(f"Unsupported catalog status: {self.catalog_status}")


def run_company_pipeline(
    cik: str,
    *,
    user_agent: str,
    forms: Collection[str],
    bronze_directory: Path,
    silver_directory: Path,
    database_path: Path,
    processing_run_id: str,
    filed_on_or_after: date | None = None,
    filed_on_or_before: date | None = None,
    limit: int | None = None,
    client: httpx.Client | None = None,
    request_interval_seconds: float = 0.5,
) -> CompanyPipelineResult:
    """Process one company, then refresh the catalog once when data is usable."""
    if not isinstance(database_path, Path):
        raise CompanyPipelineError("Database path must be a pathlib.Path value")

    processing = process_company_filings(
        cik,
        user_agent=user_agent,
        forms=forms,
        bronze_directory=bronze_directory,
        silver_directory=silver_directory,
        processing_run_id=processing_run_id,
        filed_on_or_after=filed_on_or_after,
        filed_on_or_before=filed_on_or_before,
        limit=limit,
        client=client,
        request_interval_seconds=request_interval_seconds,
    )
    if processing.usable_count == 0:
        return CompanyPipelineResult(
            _overall_status(processing.status, "SKIPPED"),
            processing,
            "SKIPPED",
            None,
            None,
        )

    try:
        catalog = refresh_silver_catalog(
            silver_directory=silver_directory,
            database_path=database_path,
        )
    except SilverCatalogError as exc:
        return CompanyPipelineResult(
            _overall_status(processing.status, "FAILED"),
            processing,
            "FAILED",
            None,
            str(exc),
        )

    return CompanyPipelineResult(
        _overall_status(processing.status, catalog.status),
        processing,
        catalog.status,
        catalog,
        None,
    )


def _overall_status(
    processing_status: Literal["COMPLETE", "PARTIAL", "FAILED"],
    catalog_status: _CatalogStatus,
) -> _PipelineStatus:
    if processing_status == "COMPLETE" and catalog_status in {"COMPLETE", "SKIPPED"}:
        return "COMPLETE"
    if processing_status == "FAILED":
        return "FAILED"
    return "PARTIAL"
