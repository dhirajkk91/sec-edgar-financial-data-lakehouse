import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import sec_edgar_lakehouse.filing_publication as publication
from sec_edgar_lakehouse import (
    ArtifactSection,
    CompleteSubmissionFile,
    DownloadedFile,
    FilingDataFile,
    FilingDiscovery,
    FilingDocument,
    FilingInventory,
    FilingReference,
    InventoryEntry,
    PublicationError,
    publish_filing,
)
from sec_edgar_lakehouse.filing_storage import (
    StorageError,
    StoredFile,
    store_downloaded_file,
)

REFERENCE = FilingReference("1122304", "0001193125-15-118890")
INVENTORY = FilingInventory(
    REFERENCE,
    (
        InventoryEntry("report.htm", ArtifactSection.SUBMITTED, "1", "10-Q", True),
        InventoryEntry(
            "package.txt", ArtifactSection.SUBMISSION_PACKAGE, None, None, True
        ),
        InventoryEntry(
            "issuer.xsd", ArtifactSection.DATA_FILE, "2", "EX-101.SCH", False
        ),
    ),
)
CONTENTS = {
    "report.htm": b"<html>report</html>",
    "package.txt": b"<SEC-DOCUMENT>0001193125-15-118890\n<SEC-HEADER>FILING\n",
    "issuer.xsd": b"<schema />",
}
DISCOVERY = FilingDiscovery(
    submitted_documents=(FilingDocument("1", "Report", "report.htm", "10-Q"),),
    complete_submission=CompleteSubmissionFile("package.txt"),
    data_files=(FilingDataFile("2", "Schema", "issuer.xsd", "EX-101.SCH"),),
    index_url=(
        "https://www.sec.gov/Archives/edgar/data/1122304/"
        "000119312515118890/0001193125-15-118890-index.html"
    ),
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
    index_content=b"<html>SEC index response</html>\n",
)
EXHIBIT_FIRST_INVENTORY = FilingInventory(
    REFERENCE,
    (
        InventoryEntry("exhibit.htm", ArtifactSection.SUBMITTED, "1", "EX-99", True),
        InventoryEntry("annual.htm", ArtifactSection.SUBMITTED, "2", "10-K", True),
        *INVENTORY.entries[1:],
    ),
)
EXHIBIT_FIRST_DISCOVERY = replace(
    DISCOVERY,
    submitted_documents=(
        FilingDocument("1", "Exhibit", "exhibit.htm", "EX-99"),
        FilingDocument("2", "Annual report", "annual.htm", "10-K"),
    ),
)
EXHIBIT_FIRST_CONTENTS = {
    "exhibit.htm": b"exhibit bytes",
    "annual.htm": b"<html>10-K filing</html>",
    "package.txt": CONTENTS["package.txt"],
    "issuer.xsd": CONTENTS["issuer.xsd"],
}


def read_manifest(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def canonical_path(bronze_directory: Path) -> Path:
    return bronze_directory / "cik=0001122304" / "accession=0001193125-15-118890"


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ((), "required submitted"),
        (
            (
                InventoryEntry(
                    "optional.htm", ArtifactSection.SUBMITTED, None, None, False
                ),
                InventoryEntry(
                    "optional.txt",
                    ArtifactSection.SUBMISSION_PACKAGE,
                    None,
                    None,
                    False,
                ),
            ),
            "required submitted",
        ),
        (
            (
                InventoryEntry(
                    "report.htm", ArtifactSection.SUBMITTED, None, None, True
                ),
            ),
            "exactly one required submission package",
        ),
        (
            (
                InventoryEntry(
                    "report.htm", ArtifactSection.SUBMITTED, None, None, True
                ),
                InventoryEntry(
                    "first.txt", ArtifactSection.SUBMISSION_PACKAGE, None, None, True
                ),
                InventoryEntry(
                    "second.txt", ArtifactSection.SUBMISSION_PACKAGE, None, None, True
                ),
            ),
            "exactly one required submission package",
        ),
    ],
)
def test_invalid_inventory_is_rejected_before_staging(
    tmp_path: Path, entries: tuple[InventoryEntry, ...], message: str
) -> None:
    inventory = FilingInventory(REFERENCE, entries)
    supplied = {entry.document_name: b"file" for entry in entries}
    bronze_directory = tmp_path / "filings"

    with pytest.raises(PublicationError, match=message):
        publish_filing(
            inventory, supplied, discovery=DISCOVERY, bronze_directory=bronze_directory
        )

    assert not bronze_directory.exists()
    assert not canonical_path(bronze_directory).exists()
    assert not (tmp_path / ".filing-staging").exists()


def test_complete_filing_is_published_with_manifest(tmp_path: Path) -> None:
    bronze_directory = tmp_path / "bronze" / "sec" / "filings"
    published = publish_filing(
        INVENTORY, CONTENTS, discovery=DISCOVERY, bronze_directory=bronze_directory
    )

    assert published.path == canonical_path(bronze_directory)
    assert published.manifest_path.parent == published.path / "manifests"
    assert published.manifest_path.name.startswith("run_id=")
    assert (published.path / "submitted" / "report.htm").read_bytes() == CONTENTS[
        "report.htm"
    ]
    assert (
        published.path / "submission-package" / "package.txt"
    ).read_bytes() == CONTENTS["package.txt"]
    assert (published.path / "sec-derived" / "issuer.xsd").read_bytes() == CONTENTS[
        "issuer.xsd"
    ]
    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "COMPLETE"
    assert manifest["source_complete"] is True
    assert [file["status"] for file in manifest["files"]] == [
        "VERIFIED",
        "VERIFIED",
        "VERIFIED",
        "VERIFIED",
        "VERIFIED",
    ]
    assert manifest["files"][0]["size_bytes"] == len(CONTENTS["report.htm"])
    assert (
        manifest["files"][0]["sha256"]
        == hashlib.sha256(CONTENTS["report.htm"]).hexdigest()
    )
    index_bytes = (published.path / "metadata" / "filing-index.html").read_bytes()
    discovery_bytes = (published.path / "metadata" / "discovery.json").read_bytes()
    assert index_bytes == DISCOVERY.index_content
    normalized = json.loads(discovery_bytes)
    assert normalized["cik"] == REFERENCE.cik
    assert normalized["accession_number"] == REFERENCE.accession_number
    assert normalized["index_url"] == DISCOVERY.index_url
    assert normalized["retrieved_at"] == DISCOVERY.retrieved_at.isoformat()
    assert normalized["inventory"] == [
        {
            "section": "submitted",
            "document_name": "report.htm",
            "description": "Report",
            "sequence": "1",
            "document_type": "10-Q",
            "required_for_source": True,
        },
        {
            "section": "submission-package",
            "document_name": "package.txt",
            "description": None,
            "sequence": None,
            "document_type": None,
            "required_for_source": True,
        },
        {
            "section": "data-file",
            "document_name": "issuer.xsd",
            "description": "Schema",
            "sequence": "2",
            "document_type": "EX-101.SCH",
            "required_for_source": False,
        },
    ]
    assert "index_content" not in normalized
    assert "SEC index response" not in discovery_bytes.decode()
    for entry, content in zip(
        manifest["files"][-2:], (index_bytes, discovery_bytes), strict=True
    ):
        assert entry["section"] == "metadata"
        assert entry["required_for_source"] is True
        assert entry["status"] == "VERIFIED"
        assert entry["size_bytes"] == len(content)
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert not any((bronze_directory.parent / ".filing-staging").iterdir())


def test_exhibit_first_does_not_hide_malformed_10k_html(tmp_path: Path) -> None:
    bronze_directory = tmp_path / "filings"
    with pytest.raises(PublicationError) as caught:
        publish_filing(
            EXHIBIT_FIRST_INVENTORY,
            EXHIBIT_FIRST_CONTENTS | {"annual.htm": b"not HTML"},
            discovery=EXHIBIT_FIRST_DISCOVERY,
            bronze_directory=bronze_directory,
        )

    assert not canonical_path(bronze_directory).exists()
    stage = caught.value.staging_path
    assert stage is not None
    assert (stage / "submitted" / "exhibit.htm").read_bytes() == b"exhibit bytes"
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["files"][0]["status"] == "VERIFIED"
    assert manifest["files"][1]["document_name"] == "annual.htm"
    assert manifest["files"][1]["status"] == "FAILED"
    assert "Primary filing HTML" in manifest["files"][1]["error"]


def test_exhibit_first_with_valid_10k_html_publishes(tmp_path: Path) -> None:
    published = publish_filing(
        EXHIBIT_FIRST_INVENTORY,
        EXHIBIT_FIRST_CONTENTS,
        discovery=EXHIBIT_FIRST_DISCOVERY,
        bronze_directory=tmp_path / "filings",
    )

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "COMPLETE"
    assert [file["status"] for file in manifest["files"][:2]] == [
        "VERIFIED",
        "VERIFIED",
    ]
    assert (published.path / "submitted" / "annual.htm").read_bytes() == (
        EXHIBIT_FIRST_CONTENTS["annual.htm"]
    )


def test_multiple_10k_10q_primary_rows_are_rejected(tmp_path: Path) -> None:
    first = replace(EXHIBIT_FIRST_INVENTORY.entries[0], document_type="10-Q")
    inventory = replace(
        EXHIBIT_FIRST_INVENTORY,
        entries=(first, *EXHIBIT_FIRST_INVENTORY.entries[1:]),
    )
    submitted = EXHIBIT_FIRST_DISCOVERY.submitted_documents
    discovery = replace(
        EXHIBIT_FIRST_DISCOVERY,
        submitted_documents=(replace(submitted[0], document_type="10-Q"), submitted[1]),
    )
    bronze_directory = tmp_path / "filings"

    with pytest.raises(PublicationError, match="Multiple primary 10-K/10-Q"):
        publish_filing(
            inventory,
            EXHIBIT_FIRST_CONTENTS,
            discovery=discovery,
            bronze_directory=bronze_directory,
        )

    assert not canonical_path(bronze_directory).exists()
    assert not (tmp_path / ".filing-staging").exists()


def test_non_10k_10q_submitted_type_has_no_primary_html_rule(tmp_path: Path) -> None:
    inventory = replace(
        INVENTORY,
        entries=(
            replace(INVENTORY.entries[0], document_type="8-K"),
            *INVENTORY.entries[1:],
        ),
    )
    discovery = replace(
        DISCOVERY,
        submitted_documents=(
            replace(DISCOVERY.submitted_documents[0], document_type="8-K"),
        ),
    )
    published = publish_filing(
        inventory,
        CONTENTS | {"report.htm": b"not HTML"},
        discovery=discovery,
        bronze_directory=tmp_path / "filings",
    )
    assert read_manifest(published.manifest_path)["status"] == "COMPLETE"


def test_missing_optional_file_is_recorded_and_does_not_block_publication(
    tmp_path: Path,
) -> None:
    bronze_directory = tmp_path / "filings"
    supplied = {
        name: content for name, content in CONTENTS.items() if name != "issuer.xsd"
    }
    published = publish_filing(
        INVENTORY, supplied, discovery=DISCOVERY, bronze_directory=bronze_directory
    )

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["source_complete"] is True
    optional = manifest["files"][2]
    assert optional == {
        "document_name": "issuer.xsd",
        "section": "data-file",
        "required_for_source": False,
        "status": "MISSING",
    }
    assert [record["status"] for record in manifest["files"][-2:]] == [
        "VERIFIED",
        "VERIFIED",
    ]
    assert not (published.path / "sec-derived" / "issuer.xsd").exists()


@pytest.mark.parametrize("metadata_name", ["filing-index.html", "discovery.json"])
@pytest.mark.parametrize("failure_kind", ["write", "verify"])
def test_metadata_failure_preserves_documents_and_blocks_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata_name: str,
    failure_kind: str,
) -> None:
    def fail_metadata(
        downloaded: DownloadedFile, *, destination_directory: Path
    ) -> StoredFile:
        if downloaded.document_name == metadata_name and failure_kind == "write":
            raise StorageError("simulated metadata write failure")
        stored = store_downloaded_file(
            downloaded, destination_directory=destination_directory
        )
        if downloaded.document_name == metadata_name:
            stored.path.write_bytes(b"x" * stored.size_bytes)
        return stored

    monkeypatch.setattr(publication, "store_downloaded_file", fail_metadata)
    bronze_directory = tmp_path / "filings"
    with pytest.raises(PublicationError, match=metadata_name) as caught:
        publish_filing(
            INVENTORY, CONTENTS, discovery=DISCOVERY, bronze_directory=bronze_directory
        )

    assert not canonical_path(bronze_directory).exists()
    stage = caught.value.staging_path
    assert stage is not None
    assert (stage / "submitted" / "report.htm").read_bytes() == CONTENTS["report.htm"]
    assert (stage / "submission-package" / "package.txt").read_bytes() == CONTENTS[
        "package.txt"
    ]
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert metadata_name in manifest["error"]
    failed = next(
        record
        for record in manifest["files"]
        if record["document_name"] == metadata_name and record["section"] == "metadata"
    )
    assert failed["status"] == "FAILED"
    assert "error" in failed


@pytest.mark.parametrize("index_content", [b"", None])
def test_missing_or_empty_index_evidence_blocks_publication(
    tmp_path: Path, index_content: bytes | None
) -> None:
    bronze_directory = tmp_path / "filings"
    with pytest.raises(PublicationError, match="filing-index.html") as caught:
        publish_filing(
            INVENTORY,
            CONTENTS,
            discovery=replace(DISCOVERY, index_content=cast(bytes, index_content)),
            bronze_directory=bronze_directory,
        )
    assert not canonical_path(bronze_directory).exists()
    assert caught.value.staging_path is not None


def test_failed_optional_storage_is_recorded_without_invented_http_details(
    tmp_path: Path,
) -> None:
    supplied = CONTENTS | {"issuer.xsd": b""}
    published = publish_filing(
        INVENTORY, supplied, discovery=DISCOVERY, bronze_directory=tmp_path / "filings"
    )

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["source_complete"] is True
    optional = manifest["files"][2]
    assert optional["status"] == "FAILED"
    assert "empty" in optional["error"]
    assert "attempts" not in optional
    assert "http_status" not in optional
    assert not (published.path / "sec-derived" / "issuer.xsd").exists()


def test_missing_required_file_preserves_verified_staging_and_failure_record(
    tmp_path: Path,
) -> None:
    bronze_directory = tmp_path / "filings"
    with pytest.raises(PublicationError) as caught:
        publish_filing(
            INVENTORY,
            {"report.htm": CONTENTS["report.htm"]},
            discovery=DISCOVERY,
            bronze_directory=bronze_directory,
        )

    assert not bronze_directory.exists()
    stage = caught.value.staging_path
    assert stage is not None
    assert stage.parent == bronze_directory.parent / ".filing-staging"
    assert (stage / "submitted" / "report.htm").read_bytes() == CONTENTS["report.htm"]
    manifest_path = next((stage / "manifests").glob("run_id=*.json"))
    manifest = read_manifest(manifest_path)
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert [file["status"] for file in manifest["files"]] == [
        "VERIFIED",
        "MISSING",
        "MISSING",
        "VERIFIED",
        "VERIFIED",
    ]


def test_required_content_error_is_visible_in_manifest_and_exception(
    tmp_path: Path,
) -> None:
    bronze_directory = tmp_path / "filings"
    with pytest.raises(PublicationError) as caught:
        publish_filing(
            INVENTORY,
            CONTENTS | {"report.htm": b"not HTML"},
            discovery=DISCOVERY,
            bronze_directory=bronze_directory,
        )

    assert not canonical_path(bronze_directory).exists()
    stage = caught.value.staging_path
    assert stage is not None
    assert (stage / "submission-package" / "package.txt").read_bytes() == CONTENTS[
        "package.txt"
    ]
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert manifest["files"][0]["status"] == "FAILED"
    reason = manifest["files"][0]["error"]
    assert "Primary filing HTML" in reason
    assert f"report.htm: {reason}" in manifest["error"]
    assert f"report.htm: {reason}" in str(caught.value)


def tamper_staged_file(monkeypatch: pytest.MonkeyPatch, tampered_name: str) -> None:
    from sec_edgar_lakehouse.filing_storage import store_downloaded_file

    def store_then_tamper(*args: object, **kwargs: object) -> StoredFile:
        stored = store_downloaded_file(*args, **kwargs)  # type: ignore[arg-type]
        if stored.document_name == tampered_name:
            stored.path.write_bytes(b"x" * len(CONTENTS[tampered_name]))
        return stored

    monkeypatch.setattr(
        "sec_edgar_lakehouse.filing_publication.store_downloaded_file",
        store_then_tamper,
    )


def test_tampered_required_file_blocks_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tamper_staged_file(monkeypatch, "report.htm")
    bronze_directory = tmp_path / "filings"
    with pytest.raises(PublicationError) as caught:
        publish_filing(
            INVENTORY, CONTENTS, discovery=DISCOVERY, bronze_directory=bronze_directory
        )

    assert not bronze_directory.exists()
    stage = caught.value.staging_path
    assert stage is not None
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert manifest["files"][0]["status"] == "FAILED"
    assert (stage / "submission-package" / "package.txt").read_bytes() == CONTENTS[
        "package.txt"
    ]


def test_tampered_optional_file_is_removed_before_partial_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tamper_staged_file(monkeypatch, "issuer.xsd")
    published = publish_filing(
        INVENTORY, CONTENTS, discovery=DISCOVERY, bronze_directory=tmp_path / "filings"
    )

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["source_complete"] is True
    assert manifest["files"][2]["status"] == "FAILED"
    assert "checksum changed" in manifest["files"][2]["error"]
    assert not (published.path / "sec-derived" / "issuer.xsd").exists()
    assert (published.path / "submitted" / "report.htm").read_bytes() == CONTENTS[
        "report.htm"
    ]


def test_optional_cleanup_failure_blocks_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tamper_staged_file(monkeypatch, "issuer.xsd")
    original_unlink = Path.unlink

    def fail_optional_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name == "issuer.xsd":
            raise OSError("cannot remove optional file")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_optional_unlink)
    bronze_directory = tmp_path / "filings"
    with pytest.raises(PublicationError) as caught:
        publish_filing(
            INVENTORY, CONTENTS, discovery=DISCOVERY, bronze_directory=bronze_directory
        )

    assert not bronze_directory.exists()
    stage = caught.value.staging_path
    assert stage is not None
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert "cleanup failed" in manifest["files"][2]["error"]
    assert (stage / "sec-derived" / "issuer.xsd").exists()


def test_optional_storage_failure_removes_leftover_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sec_edgar_lakehouse.filing_storage import StorageError, store_downloaded_file

    def fail_optional_storage(*args: object, **kwargs: object) -> StoredFile:
        downloaded = args[0]
        directory = kwargs["destination_directory"]
        assert isinstance(downloaded, DownloadedFile)
        assert isinstance(directory, Path)
        if downloaded.document_name == "issuer.xsd":
            directory.mkdir(parents=True)
            (directory / ".issuer.xsd.leftover.tmp").write_bytes(b"unverified")
            raise StorageError("optional storage failed")
        return store_downloaded_file(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "sec_edgar_lakehouse.filing_publication.store_downloaded_file",
        fail_optional_storage,
    )
    published = publish_filing(
        INVENTORY, CONTENTS, discovery=DISCOVERY, bronze_directory=tmp_path / "filings"
    )
    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["files"][2]["status"] == "FAILED"
    assert list((published.path / "sec-derived").iterdir()) == []


def test_optional_cleanup_preserves_another_inventory_filename(
    tmp_path: Path,
) -> None:
    # This valid name looks like it belongs to issuer.xsd's temp files.
    similar_name = ".issuer.xsd.notes.tmp"
    inventory = FilingInventory(
        REFERENCE,
        INVENTORY.entries[:-1]
        + (InventoryEntry(similar_name, ArtifactSection.DATA_FILE, None, None, False),)
        + INVENTORY.entries[-1:],
    )
    content = CONTENTS | {similar_name: b"valid optional content", "issuer.xsd": b""}
    discovery = replace(
        DISCOVERY,
        data_files=(
            FilingDataFile(None, None, similar_name, None),
            *DISCOVERY.data_files,
        ),
    )

    published = publish_filing(
        inventory, content, discovery=discovery, bronze_directory=tmp_path / "filings"
    )

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert [file["status"] for file in manifest["files"][2:4]] == [
        "VERIFIED",
        "FAILED",
    ]
    assert (published.path / "sec-derived" / similar_name).read_bytes() == content[
        similar_name
    ]
    assert not (published.path / "sec-derived" / "issuer.xsd").exists()


def test_existing_canonical_directory_is_never_replaced(tmp_path: Path) -> None:
    bronze_directory = tmp_path / "filings"
    existing = canonical_path(bronze_directory)
    existing.mkdir(parents=True)
    marker = existing / "original.txt"
    marker.write_bytes(b"keep me")

    with pytest.raises(PublicationError, match="already exists") as caught:
        publish_filing(
            INVENTORY, CONTENTS, discovery=DISCOVERY, bronze_directory=bronze_directory
        )

    assert marker.read_bytes() == b"keep me"
    assert list(existing.iterdir()) == [marker]
    stage = caught.value.staging_path
    assert stage is not None
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False


def test_failed_directory_move_leaves_no_partial_canonical_filing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bronze_directory = tmp_path / "filings"
    failure = OSError("move failed")

    def fail_move(source: Path, destination: Path) -> None:
        assert source.parent == bronze_directory.parent / ".filing-staging"
        assert destination == canonical_path(bronze_directory)
        prepared = read_manifest(next((source / "manifests").glob("run_id=*.json")))
        assert prepared["status"] == "COMPLETE"
        assert prepared["source_complete"] is True
        raise failure

    monkeypatch.setattr("sec_edgar_lakehouse.filing_publication.os.rename", fail_move)
    with pytest.raises(PublicationError, match="move failed") as caught:
        publish_filing(
            INVENTORY, CONTENTS, discovery=DISCOVERY, bronze_directory=bronze_directory
        )

    assert caught.value.__cause__ is failure
    assert not canonical_path(bronze_directory).exists()
    stage = caught.value.staging_path
    assert stage is not None
    assert (stage / "submitted" / "report.htm").read_bytes() == CONTENTS["report.htm"]
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False


def test_each_failed_run_gets_a_unique_staging_directory(tmp_path: Path) -> None:
    bronze_directory = tmp_path / "filings"
    stages = []
    for _ in range(2):
        with pytest.raises(PublicationError) as caught:
            publish_filing(
                INVENTORY, {}, discovery=DISCOVERY, bronze_directory=bronze_directory
            )
        stages.append(caught.value.staging_path)
    assert stages[0] != stages[1]
    assert all(stage is not None and stage.is_dir() for stage in stages)
    assert not bronze_directory.exists()
