"""Before downloading, decide which listed files Bronze archives.

Bronze keeps the files Silver reads and the evidence around them: the extracted
XBRL instance, which is required when the filing lists one, plus the XBRL schema
and calculation linkbase, which are optional. Everything else the SEC lists
(the filing HTML, exhibits, images, the other linkbases and the complete
submission text) stays recorded in the saved index page and can be fetched again
by URL, but is not downloaded. That keeps a filing at a few MB instead of ~25 MB,
which is what makes the S&P 500 fit in free storage (decision 17).

The inventory keeps the index order and checks that filenames cannot collide.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote

from sec_edgar_lakehouse.bronze.filing_discovery import FilingDataFile, FilingDiscovery
from sec_edgar_lakehouse.filing_reference import FilingReference

# The archive rules have a name so a manifest says which rules produced it.
ARCHIVE_POLICY = "xbrl-v1"
# SEC labels for the XBRL instance it extracts from an inline XBRL filing.
INSTANCE_DESCRIPTIONS = frozenset(
    {"extracted xbrl instance document", "xbrl instance document"}
)
# Small files that explain the instance: company-specific concepts and their sums.
OPTIONAL_DATA_TYPES = frozenset({"EX-101.SCH", "EX-101.CAL"})


class ArtifactSection(StrEnum):
    """The SEC listing section determines where an artifact goes in Bronze."""

    DATA_FILE = "data-file"


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """One listed file and whether losing it would make the source incomplete."""

    document_name: str
    section: ArtifactSection
    sequence: str | None
    document_type: str | None
    required_for_source: bool


@dataclass(frozen=True, slots=True)
class FilingInventory:
    """An ordered artifact inventory for one filing."""

    reference: FilingReference
    entries: tuple[InventoryEntry, ...]


class InventoryError(Exception):
    """Discovered artifacts could not form a valid inventory."""


def is_instance(data_file: FilingDataFile) -> bool:
    """The extracted XBRL instance, recognised by the SEC's description and XML name."""
    return (
        (data_file.description or "").strip().casefold() in INSTANCE_DESCRIPTIONS
        and data_file.document_name.lower().endswith(".xml")
    )


def archive_requirement(data_file: FilingDataFile) -> bool | None:
    """True for a required file, False for an optional one, None when not archived."""
    if is_instance(data_file):
        return True
    if (data_file.document_type or "").strip().upper() in OPTIONAL_DATA_TYPES:
        return False
    return None


def archived_data_files(
    discovery: FilingDiscovery,
) -> Iterable[tuple[FilingDataFile, bool]]:
    """The listed data files Bronze keeps, in index order, with their requirement."""
    for data_file in discovery.data_files:
        required = archive_requirement(data_file)
        if required is not None:
            yield data_file, required


def build_filing_inventory(
    reference: FilingReference, discovery: FilingDiscovery
) -> FilingInventory:
    """List the archived files in index order.

    A listing without submitted documents isn't a real filing page. Unsafe names
    or names that collide on a case-insensitive filesystem raise InventoryError
    before any files are downloaded.
    """
    if not isinstance(reference, FilingReference):
        raise InventoryError("Reference must be a FilingReference")
    if not isinstance(discovery, FilingDiscovery):
        raise InventoryError("Discovery must be a FilingDiscovery")
    if not discovery.submitted_documents:
        raise InventoryError("Inventory requires at least one submitted document")

    entries = [
        InventoryEntry(
            document_name=data_file.document_name,
            section=ArtifactSection.DATA_FILE,
            sequence=data_file.sequence,
            document_type=data_file.document_type,
            required_for_source=required,
        )
        for data_file, required in archived_data_files(discovery)
    ]

    seen: dict[str, InventoryEntry] = {}
    for entry in entries:
        _validate_filename(entry.document_name)
        # Local Windows/macOS storage may treat differently cased names as one file.
        key = entry.document_name.casefold()
        previous = seen.get(key)
        if previous is not None:
            raise InventoryError(
                f"Conflicting filenames: {previous.document_name!r} and "
                f"{entry.document_name!r}"
            )
        seen[key] = entry

    return FilingInventory(reference=reference, entries=tuple(entries))


def _validate_filename(name: str) -> None:
    """Recheck names supplied in memory; discovery is not the only caller of this code."""
    if not isinstance(name, str) or not name:
        raise InventoryError("Document filename must be a non-empty string")
    decoded_name = unquote(name)
    if (
        decoded_name in {".", ".."}
        or "/" in decoded_name
        or "\\" in decoded_name
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded_name)
    ):
        raise InventoryError(f"Unsafe document filename: {name!r}")
