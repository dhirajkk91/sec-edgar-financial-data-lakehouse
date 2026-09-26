"""Domain types for the SEC EDGAR financial data lakehouse."""

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

# Keep the package root as the stable import surface for callers.
__all__ = [
    "ArtifactSection",
    "CompleteSubmissionFile",
    "DiscoveryError",
    "DownloadError",
    "DownloadFailure",
    "DownloadedFile",
    "FilingDataFile",
    "FilingDiscovery",
    "FilingDocument",
    "FilingInventory",
    "FilingReference",
    "IngestionResult",
    "InventoryEntry",
    "InventoryError",
    "PublicationError",
    "PublishedFiling",
    "RejectedOccurrence",
    "SilverDimension",
    "SilverExtraction",
    "SilverExtractionError",
    "SilverFact",
    "SilverInputError",
    "StorageError",
    "StoredFile",
    "build_filing_inventory",
    "discover_filing",
    "download_filing_file",
    "extract_filing_facts",
    "ingest_filing",
    "publish_filing",
    "store_downloaded_file",
]
