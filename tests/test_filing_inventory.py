from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from sec_edgar_lakehouse import (
    ArtifactSection,
    CompleteSubmissionFile,
    FilingDataFile,
    FilingDiscovery,
    FilingDocument,
    FilingInventory,
    FilingReference,
    InventoryEntry,
    InventoryError,
    build_filing_inventory,
)

REFERENCE = FilingReference("1122304", "0001193125-15-118890")
# Sequence numbers are deliberately out of order: inventory follows SEC row order.
DISCOVERY = FilingDiscovery(
    submitted_documents=(
        FilingDocument("2", "Report", "report.htm", "10-Q"),
        FilingDocument("1", "Exhibit", "exhibit.htm", "Future-Type"),
        FilingDocument(None, None, "other.htm", None),
    ),
    complete_submission=CompleteSubmissionFile("package.txt"),
    data_files=(
        FilingDataFile("5", "Schema", "issuer.xsd", "EX-101.SCH"),
        FilingDataFile("4", "Future data", "future.xml", "Unknown-Type"),
        FilingDataFile(None, None, "optional.xml", None),
    ),
    index_url="https://www.sec.gov/Archives/edgar/data/1122304/index.html",
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
    index_content=b"<html>index</html>",
)


def test_inventory_preserves_order_metadata_and_source_requirements() -> None:
    inventory = build_filing_inventory(REFERENCE, DISCOVERY)

    assert isinstance(inventory, FilingInventory)
    assert inventory.reference is REFERENCE
    assert inventory.entries == (
        InventoryEntry("report.htm", ArtifactSection.SUBMITTED, "2", "10-Q", True),
        InventoryEntry(
            "exhibit.htm", ArtifactSection.SUBMITTED, "1", "Future-Type", True
        ),
        InventoryEntry("other.htm", ArtifactSection.SUBMITTED, None, None, True),
        InventoryEntry(
            "package.txt", ArtifactSection.SUBMISSION_PACKAGE, None, None, True
        ),
        InventoryEntry(
            "issuer.xsd", ArtifactSection.DATA_FILE, "5", "EX-101.SCH", False
        ),
        InventoryEntry(
            "future.xml", ArtifactSection.DATA_FILE, "4", "Unknown-Type", False
        ),
        InventoryEntry("optional.xml", ArtifactSection.DATA_FILE, None, None, False),
    )


def test_no_data_files_is_valid() -> None:
    inventory = build_filing_inventory(REFERENCE, replace(DISCOVERY, data_files=()))
    assert len(inventory.entries) == 4
    assert inventory.entries[-1].section is ArtifactSection.SUBMISSION_PACKAGE


def test_inventory_and_entry_are_frozen_and_slotted() -> None:
    inventory = build_filing_inventory(REFERENCE, DISCOVERY)
    with pytest.raises(FrozenInstanceError):
        inventory.entries = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        inventory.entries[0].required_for_source = False  # type: ignore[misc]
    assert not hasattr(inventory, "__dict__")
    assert not hasattr(inventory.entries[0], "__dict__")


def test_section_string_values() -> None:
    assert list(ArtifactSection) == ["submitted", "submission-package", "data-file"]
    assert str(ArtifactSection.SUBMITTED) == "submitted"


def test_discovery_is_unchanged_and_build_is_deterministic() -> None:
    before = FilingDiscovery(
        tuple(replace(document) for document in DISCOVERY.submitted_documents),
        replace(DISCOVERY.complete_submission),
        tuple(replace(data_file) for data_file in DISCOVERY.data_files),
        DISCOVERY.index_url,
        DISCOVERY.retrieved_at,
        DISCOVERY.index_content,
    )
    first = build_filing_inventory(REFERENCE, DISCOVERY)
    assert DISCOVERY == before
    assert build_filing_inventory(REFERENCE, DISCOVERY) == first


def test_requires_submitted_documents() -> None:
    with pytest.raises(InventoryError, match="at least one submitted document"):
        build_filing_inventory(REFERENCE, replace(DISCOVERY, submitted_documents=()))


def discovery_with_name(section: ArtifactSection, name: str) -> FilingDiscovery:
    if section is ArtifactSection.SUBMITTED:
        return replace(
            DISCOVERY,
            submitted_documents=(
                replace(DISCOVERY.submitted_documents[0], document_name=name),
            ),
        )
    if section is ArtifactSection.SUBMISSION_PACKAGE:
        return replace(DISCOVERY, complete_submission=CompleteSubmissionFile(name))
    return replace(
        DISCOVERY, data_files=(replace(DISCOVERY.data_files[0], document_name=name),)
    )


@pytest.mark.parametrize("section", list(ArtifactSection))
@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../report.htm",
        "folder/report.htm",
        "folder\\report.htm",
        "report\x00.htm",
        "report\n.htm",
        "report\x7f.htm",
        "%2e%2e%2freport.htm",
        "folder%5Creport.htm",
        "folder%2Freport.htm",
        "%2e",
        "%2e%2e",
        "report%0a.htm",
    ],
)
def test_revalidates_names_in_every_section(
    section: ArtifactSection, name: str
) -> None:
    with pytest.raises(InventoryError, match="filename"):
        build_filing_inventory(REFERENCE, discovery_with_name(section, name))


@pytest.mark.parametrize(
    "section", [ArtifactSection.SUBMITTED, ArtifactSection.DATA_FILE]
)
def test_rejects_duplicates_within_section(section: ArtifactSection) -> None:
    if section is ArtifactSection.SUBMITTED:
        discovery = replace(
            DISCOVERY,
            submitted_documents=(
                DISCOVERY.submitted_documents[0],
                DISCOVERY.submitted_documents[0],
            ),
        )
        name = "report.htm"
    else:
        discovery = replace(
            DISCOVERY,
            data_files=(
                DISCOVERY.data_files[0],
                DISCOVERY.data_files[0],
            ),
        )
        name = "issuer.xsd"
    with pytest.raises(InventoryError) as caught:
        build_filing_inventory(REFERENCE, discovery)
    assert str(caught.value).count(f"{name!r} ({section})") == 2


@pytest.mark.parametrize(
    ("section", "name", "conflicting_section", "conflicting_name"),
    [
        (
            ArtifactSection.DATA_FILE,
            "report.htm",
            ArtifactSection.SUBMITTED,
            "report.htm",
        ),
        (
            ArtifactSection.SUBMISSION_PACKAGE,
            "report.htm",
            ArtifactSection.SUBMITTED,
            "report.htm",
        ),
        (
            ArtifactSection.DATA_FILE,
            "package.txt",
            ArtifactSection.SUBMISSION_PACKAGE,
            "package.txt",
        ),
        (
            ArtifactSection.DATA_FILE,
            "REPORT.HTM",
            ArtifactSection.SUBMITTED,
            "report.htm",
        ),
        (
            ArtifactSection.SUBMISSION_PACKAGE,
            "REPORT.HTM",
            ArtifactSection.SUBMITTED,
            "report.htm",
        ),
        (
            ArtifactSection.SUBMITTED,
            "PACKAGE.TXT",
            ArtifactSection.SUBMISSION_PACKAGE,
            "package.txt",
        ),
    ],
)
def test_rejects_cross_section_and_case_collisions(
    section: ArtifactSection,
    name: str,
    conflicting_section: ArtifactSection,
    conflicting_name: str,
) -> None:
    with pytest.raises(InventoryError) as caught:
        build_filing_inventory(REFERENCE, discovery_with_name(section, name))
    message = str(caught.value)
    assert f"{name!r} ({section})" in message
    assert f"{conflicting_name!r} ({conflicting_section})" in message


def test_rejects_invalid_reference_type() -> None:
    with pytest.raises(InventoryError, match="FilingReference"):
        build_filing_inventory("reference", DISCOVERY)  # type: ignore[arg-type]


def test_rejects_invalid_discovery_type() -> None:
    with pytest.raises(InventoryError, match="FilingDiscovery"):
        build_filing_inventory(REFERENCE, None)  # type: ignore[arg-type]
