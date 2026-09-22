import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from sec_edgar_lakehouse import (
    ArtifactSection,
    DownloadedFile,
    FilingInventory,
    FilingReference,
    InventoryEntry,
    PublicationError,
    publish_filing,
)
from sec_edgar_lakehouse.filing_storage import StoredFile

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
    "package.txt": b"complete submission",
    "issuer.xsd": b"<schema />",
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
        publish_filing(inventory, supplied, bronze_directory=bronze_directory)

    assert not bronze_directory.exists()
    assert not canonical_path(bronze_directory).exists()
    assert not (tmp_path / ".filing-staging").exists()


def test_complete_filing_is_published_with_manifest(tmp_path: Path) -> None:
    bronze_directory = tmp_path / "bronze" / "sec" / "filings"
    published = publish_filing(INVENTORY, CONTENTS, bronze_directory=bronze_directory)

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
    ]
    assert manifest["files"][0]["size_bytes"] == len(CONTENTS["report.htm"])
    assert (
        manifest["files"][0]["sha256"]
        == hashlib.sha256(CONTENTS["report.htm"]).hexdigest()
    )
    assert not any((bronze_directory.parent / ".filing-staging").iterdir())


def test_missing_optional_file_is_recorded_and_does_not_block_publication(
    tmp_path: Path,
) -> None:
    bronze_directory = tmp_path / "filings"
    supplied = {
        name: content for name, content in CONTENTS.items() if name != "issuer.xsd"
    }
    published = publish_filing(INVENTORY, supplied, bronze_directory=bronze_directory)

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["source_complete"] is True
    optional = manifest["files"][-1]
    assert optional == {
        "document_name": "issuer.xsd",
        "section": "data-file",
        "required_for_source": False,
        "status": "MISSING",
    }
    assert not (published.path / "sec-derived" / "issuer.xsd").exists()


def test_failed_optional_storage_is_recorded_without_invented_http_details(
    tmp_path: Path,
) -> None:
    supplied = CONTENTS | {"issuer.xsd": b""}
    published = publish_filing(
        INVENTORY, supplied, bronze_directory=tmp_path / "filings"
    )

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["source_complete"] is True
    optional = manifest["files"][-1]
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
    ]


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
        publish_filing(INVENTORY, CONTENTS, bronze_directory=bronze_directory)

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
        INVENTORY, CONTENTS, bronze_directory=tmp_path / "filings"
    )

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["source_complete"] is True
    assert manifest["files"][-1]["status"] == "FAILED"
    assert "checksum changed" in manifest["files"][-1]["error"]
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
        publish_filing(INVENTORY, CONTENTS, bronze_directory=bronze_directory)

    assert not bronze_directory.exists()
    stage = caught.value.staging_path
    assert stage is not None
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert "cleanup failed" in manifest["files"][-1]["error"]
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
        INVENTORY, CONTENTS, bronze_directory=tmp_path / "filings"
    )
    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["files"][-1]["status"] == "FAILED"
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

    published = publish_filing(
        inventory, content, bronze_directory=tmp_path / "filings"
    )

    manifest = read_manifest(published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert [file["status"] for file in manifest["files"][-2:]] == [
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
        publish_filing(INVENTORY, CONTENTS, bronze_directory=bronze_directory)

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
        publish_filing(INVENTORY, CONTENTS, bronze_directory=bronze_directory)

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
            publish_filing(INVENTORY, {}, bronze_directory=bronze_directory)
        stages.append(caught.value.staging_path)
    assert stages[0] != stages[1]
    assert all(stage is not None and stage.is_dir() for stage in stages)
    assert not bronze_directory.exists()
