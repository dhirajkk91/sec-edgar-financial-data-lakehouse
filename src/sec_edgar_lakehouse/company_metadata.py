"""Load existing metadata evidence after a completed local company run."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sec_edgar_lakehouse.company_processing import CompanyFilingProcessResult
from sec_edgar_lakehouse.company_run import CompanyRunResult
from sec_edgar_lakehouse.filing_fiscal_metadata_storage import (
    FilingFiscalMetadataLoadResult,
    FiscalMetadataLoadError,
    load_filing_fiscal_metadata,
)
from sec_edgar_lakehouse.filing_metadata import (
    FilingMetadataError,
    FilingMetadataLoadResult,
    load_filing_metadata,
)
from sec_edgar_lakehouse.filing_reference import FilingReference, _normalize_cik


class CompanyMetadataLoadError(Exception):
    """Coordinator input or run identity prevents metadata loading."""


@dataclass(frozen=True, slots=True)
class CompanyFiscalMetadataResult:
    reference: FilingReference
    outcome: Literal["INSERTED", "ALREADY_EXISTS", "FAILED", "NOT_ATTEMPTED"]
    load_result: FilingFiscalMetadataLoadResult | None
    error_stage: Literal["FILING_METADATA", "FISCAL_METADATA"] | None
    error_message: str | None

    @property
    def status(self) -> Literal["COMPLETE", "PARTIAL", "FAILED", "NOT_ATTEMPTED"]:
        if self.outcome in {"INSERTED", "ALREADY_EXISTS"}:
            if self.load_result is None:
                raise ValueError("A successful fiscal load requires a loader result")
            return self.load_result.status
        return "FAILED" if self.outcome == "FAILED" else "NOT_ATTEMPTED"


@dataclass(frozen=True, slots=True)
class CompanyMetadataLoadResult:
    status: Literal["COMPLETE", "PARTIAL", "FAILED", "SKIPPED"]
    company_run: CompanyRunResult
    database_path: Path
    filing_metadata_result: FilingMetadataLoadResult | None
    filing_metadata_error: str | None
    fiscal_results: tuple[CompanyFiscalMetadataResult, ...]
    skip_reason: str | None


def _validate_identity(company_run: CompanyRunResult, database: Path) -> None:
    pipeline = company_run.pipeline_result
    if pipeline is None or pipeline.catalog_result is None:
        raise CompanyMetadataLoadError("Eligible run requires a catalog result")
    catalog_path = pipeline.catalog_result.database_path
    if not isinstance(catalog_path, Path):
        raise CompanyMetadataLoadError("Catalog database must be a pathlib.Path value")
    try:
        catalog_database = catalog_path.resolve()
        cik = _normalize_cik(pipeline.processing.discovery.cik)
    except (OSError, TypeError, ValueError) as exc:
        raise CompanyMetadataLoadError(str(exc)) from exc
    if database != catalog_database:
        raise CompanyMetadataLoadError(
            "Target database does not match catalog database"
        )
    if company_run.run_id != pipeline.processing.processing_run_id:
        raise CompanyMetadataLoadError(
            "Company run ID does not match processing run ID"
        )
    path = company_run.run_record_path
    if not isinstance(path, Path):
        raise CompanyMetadataLoadError("Run record must be a pathlib.Path value")
    if (
        path.name != "run.json"
        or path.parent.name != f"run_id={company_run.run_id}"
        or path.parent.parent.name != f"cik={cik}"
    ):
        raise CompanyMetadataLoadError(
            "Run record path does not match run ID and normalized CIK directories"
        )


def _load_fiscal(
    item: CompanyFilingProcessResult,
    filing_metadata: FilingMetadataLoadResult,
    database: Path,
) -> CompanyFiscalMetadataResult:
    reference = item.filing.reference
    blocking_issues = tuple(
        issue
        for issue in filing_metadata.issues
        if issue.cik == reference.cik
        and issue.accession_number == reference.accession_number
        and issue.reason_code in {"MISSING_METADATA", "METADATA_CONFLICT"}
    )
    if blocking_issues:
        return CompanyFiscalMetadataResult(
            reference,
            "NOT_ATTEMPTED",
            None,
            "FILING_METADATA",
            "; ".join(
                f"{issue.reason_code}: {issue.message}" for issue in blocking_issues
            ),
        )
    if item.bronze_path is None or item.bronze_manifest_path is None:
        return CompanyFiscalMetadataResult(
            reference,
            "FAILED",
            None,
            "FISCAL_METADATA",
            "Usable selected filing is missing its Bronze directory or manifest path",
        )
    try:
        loaded = load_filing_fiscal_metadata(
            item.bronze_path, item.bronze_manifest_path, database_path=database
        )
    except FiscalMetadataLoadError as exc:
        return CompanyFiscalMetadataResult(
            reference, "FAILED", None, "FISCAL_METADATA", str(exc)
        )
    return CompanyFiscalMetadataResult(reference, loaded.outcome, loaded, None, None)


def load_company_run_metadata(
    company_run: CompanyRunResult, *, database_path: Path
) -> CompanyMetadataLoadResult:
    """Load filing metadata once, then fiscal metadata for usable selected filings.

    Status describes metadata loading independently of the original company run.
    Existing loaders own evidence verification and persistence; this coordinator
    never rewrites run evidence or rolls back previously published outputs.
    """
    if not isinstance(company_run, CompanyRunResult):
        raise CompanyMetadataLoadError("Company run must be a CompanyRunResult value")
    if not isinstance(database_path, Path):
        raise CompanyMetadataLoadError("Database must be a pathlib.Path value")
    try:
        database = database_path.resolve()
    except OSError as exc:
        raise CompanyMetadataLoadError(str(exc)) from exc
    pipeline = company_run.pipeline_result
    skip_reason = None
    usable: tuple[CompanyFilingProcessResult, ...] = ()
    if pipeline is None:
        skip_reason = "Company run has no pipeline result"
    elif pipeline.catalog_status not in {"COMPLETE", "PARTIAL"}:
        skip_reason = f"Catalog status {pipeline.catalog_status} is not eligible"
    else:
        usable = tuple(
            item
            for item in pipeline.processing.filing_results
            if item.outcome in {"COMPLETE", "PARTIAL", "SKIPPED"}
        )
        if not usable:
            skip_reason = "Company run has no usable selected filings"
    if skip_reason is not None:
        return CompanyMetadataLoadResult(
            "SKIPPED", company_run, database, None, None, (), skip_reason
        )
    _validate_identity(company_run, database)
    try:
        filing_metadata = load_filing_metadata(
            run_record_path=company_run.run_record_path, database_path=database
        )
    except FilingMetadataError as exc:
        error = str(exc)
        return CompanyMetadataLoadResult(
            "FAILED",
            company_run,
            database,
            None,
            error,
            tuple(
                CompanyFiscalMetadataResult(
                    item.filing.reference,
                    "NOT_ATTEMPTED",
                    None,
                    "FILING_METADATA",
                    error,
                )
                for item in usable
            ),
            None,
        )
    fiscal_results = tuple(
        _load_fiscal(item, filing_metadata, database) for item in usable
    )
    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    if not any(item.status in {"COMPLETE", "PARTIAL"} for item in fiscal_results):
        status = "FAILED"
    elif filing_metadata.status == "PARTIAL" or any(
        item.status != "COMPLETE" for item in fiscal_results
    ):
        status = "PARTIAL"
    else:
        status = "COMPLETE"
    return CompanyMetadataLoadResult(
        status, company_run, database, filing_metadata, None, fiscal_results, None
    )
