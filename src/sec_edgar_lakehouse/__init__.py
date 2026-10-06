"""Domain types for the SEC EDGAR financial data lakehouse."""

from sec_edgar_lakehouse.company_filings import (
    CompanyFiling,
    CompanyFilingsDiscovery,
    CompanyFilingsDiscoveryError,
    discover_company_filings,
    select_company_filings,
)
from sec_edgar_lakehouse.company_metadata import (
    CompanyFiscalMetadataResult,
    CompanyMetadataLoadError,
    CompanyMetadataLoadResult,
    load_company_run_metadata,
)
from sec_edgar_lakehouse.company_pipeline import (
    CompanyPipelineError,
    CompanyPipelineResult,
    run_company_pipeline,
)
from sec_edgar_lakehouse.company_processing import (
    CompanyFilingProcessResult,
    CompanyProcessingError,
    CompanyProcessingResult,
    process_company_filings,
)
from sec_edgar_lakehouse.company_run import (
    CompanyRunError,
    CompanyRunResult,
    execute_company_pipeline_run,
)
from sec_edgar_lakehouse.filing_discovery import (
    CompleteSubmissionFile,
    DiscoveryError,
    FilingDataFile,
    FilingDiscovery,
    FilingDocument,
    discover_filing,
)
from sec_edgar_lakehouse.filing_download import (
    DownloadedFile,
    DownloadError,
    download_filing_file,
)
from sec_edgar_lakehouse.filing_fiscal_metadata import (
    FilingFiscalMetadata,
    FiscalMetadataExtractionError,
    FiscalMetadataInputError,
    FiscalMetadataIssue,
    FiscalMetadataOccurrence,
    extract_filing_fiscal_metadata,
)
from sec_edgar_lakehouse.filing_fiscal_metadata_storage import (
    FilingFiscalMetadataLoadResult,
    FiscalMetadataLoadError,
    load_filing_fiscal_metadata,
)
from sec_edgar_lakehouse.filing_ingestion import (
    DownloadFailure,
    IngestionResult,
    ingest_filing,
)
from sec_edgar_lakehouse.filing_inventory import (
    ArtifactSection,
    FilingInventory,
    InventoryEntry,
    InventoryError,
    build_filing_inventory,
)
from sec_edgar_lakehouse.filing_metadata import (
    FilingMetadataError,
    FilingMetadataIssue,
    FilingMetadataLoadResult,
    load_filing_metadata,
)
from sec_edgar_lakehouse.filing_publication import (
    PublicationError,
    PublishedFiling,
    publish_filing,
)
from sec_edgar_lakehouse.filing_reference import FilingReference
from sec_edgar_lakehouse.filing_storage import (
    StorageError,
    StoredFile,
    store_downloaded_file,
)
from sec_edgar_lakehouse.silver_catalog import (
    SilverCatalogError,
    SilverCatalogResult,
    refresh_silver_catalog,
)
from sec_edgar_lakehouse.silver_extraction import (
    SilverExtractionError,
    SilverInputError,
    extract_filing_facts,
)
from sec_edgar_lakehouse.silver_models import (
    RejectedOccurrence,
    SilverDimension,
    SilverExtraction,
    SilverFact,
)
from sec_edgar_lakehouse.silver_parquet import (
    SilverParquetFiles,
    SilverWriteError,
    write_silver_parquet,
)
from sec_edgar_lakehouse.silver_publication import (
    SilverPublicationError,
    SilverPublicationResult,
    publish_silver_extraction,
)

# Keep the package root as the stable import surface for callers.
__all__ = [
    "ArtifactSection",
    "CompanyFiling",
    "CompanyFilingProcessResult",
    "CompanyFilingsDiscovery",
    "CompanyFilingsDiscoveryError",
    "CompanyFiscalMetadataResult",
    "CompanyMetadataLoadError",
    "CompanyMetadataLoadResult",
    "CompanyPipelineError",
    "CompanyPipelineResult",
    "CompanyProcessingError",
    "CompanyProcessingResult",
    "CompanyRunError",
    "CompanyRunResult",
    "CompleteSubmissionFile",
    "DiscoveryError",
    "DownloadError",
    "DownloadFailure",
    "DownloadedFile",
    "FilingDataFile",
    "FilingDiscovery",
    "FilingDocument",
    "FilingFiscalMetadata",
    "FilingFiscalMetadataLoadResult",
    "FilingInventory",
    "FilingMetadataError",
    "FilingMetadataIssue",
    "FilingMetadataLoadResult",
    "FilingReference",
    "FiscalMetadataExtractionError",
    "FiscalMetadataInputError",
    "FiscalMetadataIssue",
    "FiscalMetadataLoadError",
    "FiscalMetadataOccurrence",
    "IngestionResult",
    "InventoryEntry",
    "InventoryError",
    "PublicationError",
    "PublishedFiling",
    "RejectedOccurrence",
    "SilverCatalogError",
    "SilverCatalogResult",
    "SilverDimension",
    "SilverExtraction",
    "SilverExtractionError",
    "SilverFact",
    "SilverInputError",
    "SilverParquetFiles",
    "SilverPublicationError",
    "SilverPublicationResult",
    "SilverWriteError",
    "StorageError",
    "StoredFile",
    "build_filing_inventory",
    "discover_company_filings",
    "discover_filing",
    "download_filing_file",
    "execute_company_pipeline_run",
    "extract_filing_facts",
    "extract_filing_fiscal_metadata",
    "ingest_filing",
    "load_company_run_metadata",
    "load_filing_fiscal_metadata",
    "load_filing_metadata",
    "process_company_filings",
    "publish_filing",
    "publish_silver_extraction",
    "refresh_silver_catalog",
    "run_company_pipeline",
    "select_company_filings",
    "store_downloaded_file",
    "write_silver_parquet",
]
