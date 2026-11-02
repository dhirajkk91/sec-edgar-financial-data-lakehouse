"""The archive rules: which listed files Bronze keeps, and which names it refuses."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from sec_edgar_lakehouse import (
    ArtifactSection,
    CompleteSubmissionFile,
    FilingDataFile,
    FilingDiscovery,
    FilingDocument,
    FilingReference,
    InventoryEntry,
    InventoryError,
    build_filing_inventory,
)

REFERENCE = FilingReference("1122304", "0001193125-15-118890")
DISCOVERY = FilingDiscovery(
    submitted_documents=(
        FilingDocument("1", "Quarterly report", "report.htm", "10-Q"),
        FilingDocument("2", "Exhibit", "exhibit.htm", "EX-99"),
    ),
    complete_submission=CompleteSubmissionFile("package.txt"),
    data_files=(
        FilingDataFile("3", "XBRL schema", "issuer.xsd", "EX-101.SCH"),
        FilingDataFile("4", "Label linkbase", "issuer_lab.xml", "EX-101.LAB"),
        FilingDataFile("5", "Calculation linkbase", "issuer_cal.xml", "EX-101.CAL"),
        FilingDataFile(
            "6", "EXTRACTED XBRL INSTANCE DOCUMENT", "issuer_htm.xml", "XML"
        ),
        FilingDataFile(None, None, "Financial_Report.xlsx", None),
    ),
    index_url="https://www.sec.gov/Archives/edgar/data/1122304/index.html",
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
    index_content=b"<html></html>",
)


def test_keeps_the_instance_schema_and_calculations_in_index_order() -> None:
    inventory = build_filing_inventory(REFERENCE, DISCOVERY)

    assert inventory.entries == (
        InventoryEntry(
            "issuer.xsd", ArtifactSection.DATA_FILE, "3", "EX-101.SCH", False
        ),
        InventoryEntry(
            "issuer_cal.xml", ArtifactSection.DATA_FILE, "5", "EX-101.CAL", False
        ),
        InventoryEntry("issuer_htm.xml", ArtifactSection.DATA_FILE, "6", "XML", True),
    )
    # Building is deterministic and leaves the discovery untouched.
    assert build_filing_inventory(REFERENCE, DISCOVERY) == inventory


def test_a_filing_without_xbrl_archives_nothing() -> None:
    discovery = replace(DISCOVERY, data_files=())

    assert build_filing_inventory(REFERENCE, discovery).entries == ()


def test_requires_a_real_filing_listing() -> None:
    with pytest.raises(InventoryError, match="at least one submitted document"):
        build_filing_inventory(REFERENCE, replace(DISCOVERY, submitted_documents=()))
    with pytest.raises(InventoryError, match="FilingReference"):
        build_filing_inventory("x", DISCOVERY)  # type: ignore[arg-type]
    with pytest.raises(InventoryError, match="FilingDiscovery"):
        build_filing_inventory(REFERENCE, "x")  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["..", "a/b.xml", "a\\b.xml", "bad\x00.xml", "%2e%2e"])
def test_rejects_unsafe_archived_names(name: str) -> None:
    discovery = replace(
        DISCOVERY, data_files=(FilingDataFile("1", "XBRL schema", name, "EX-101.SCH"),)
    )

    with pytest.raises(InventoryError, match="Unsafe document filename"):
        build_filing_inventory(REFERENCE, discovery)


def test_rejects_names_that_collide_on_a_case_insensitive_disk() -> None:
    discovery = replace(
        DISCOVERY,
        data_files=(
            FilingDataFile("1", "XBRL schema", "Issuer.xsd", "EX-101.SCH"),
            FilingDataFile("2", "XBRL schema", "issuer.XSD", "EX-101.SCH"),
        ),
    )

    with pytest.raises(InventoryError, match="Conflicting filenames"):
        build_filing_inventory(REFERENCE, discovery)
