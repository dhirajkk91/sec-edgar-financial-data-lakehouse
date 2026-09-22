"""Apply source-completeness requirements to discovered filing artifacts."""

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote

from sec_edgar_lakehouse.filing_discovery import FilingDiscovery
from sec_edgar_lakehouse.filing_reference import FilingReference


class ArtifactSection(StrEnum):
    SUBMITTED = "submitted"
    SUBMISSION_PACKAGE = "submission-package"
    DATA_FILE = "data-file"


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """One discovered artifact and its source-completeness requirement."""

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


def build_filing_inventory(
    reference: FilingReference, discovery: FilingDiscovery
) -> FilingInventory:
    """Build a validated inventory in submitted, package, then data-file order."""
    if not isinstance(reference, FilingReference):
        raise InventoryError("Reference must be a FilingReference")
    if not isinstance(discovery, FilingDiscovery):
        raise InventoryError("Discovery must be a FilingDiscovery")
    if not discovery.submitted_documents:
        raise InventoryError("Inventory requires at least one submitted document")

    entries: list[InventoryEntry] = []
    for document in discovery.submitted_documents:
        entries.append(
            InventoryEntry(
                document_name=document.document_name,
                section=ArtifactSection.SUBMITTED,
                sequence=document.sequence,
                document_type=document.document_type,
                required_for_source=True,
            )
        )
    entries.append(
        InventoryEntry(
            document_name=discovery.complete_submission.document_name,
            section=ArtifactSection.SUBMISSION_PACKAGE,
            sequence=None,
            document_type=None,
            required_for_source=True,
        )
    )
    # Data Files are useful to keep, but their absence doesn't make the source incomplete.
    for data_file in discovery.data_files:
        entries.append(
            InventoryEntry(
                document_name=data_file.document_name,
                section=ArtifactSection.DATA_FILE,
                sequence=data_file.sequence,
                document_type=data_file.document_type,
                required_for_source=False,
            )
        )

    seen: dict[str, InventoryEntry] = {}
    for entry in entries:
        _validate_filename(entry.document_name)
        # Local Windows/macOS storage may treat differently cased names as one file.
        key = entry.document_name.casefold()
        previous = seen.get(key)
        if previous is not None:
            raise InventoryError(
                f"Conflicting filenames: {previous.document_name!r} ({previous.section}) "
                f"and {entry.document_name!r} ({entry.section})"
            )
        seen[key] = entry

    return FilingInventory(reference=reference, entries=tuple(entries))


def _validate_filename(name: str) -> None:
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
