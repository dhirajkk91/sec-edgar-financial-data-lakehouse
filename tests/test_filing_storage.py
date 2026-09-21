import hashlib
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sec_edgar_lakehouse import (
    DownloadedFile,
    StorageError,
    store_downloaded_file,
)

CONTENT = b"\x00SEC filing\xff\r\n"


def test_stores_verified_bytes_and_creates_parents(tmp_path: Path) -> None:
    destination = tmp_path / "missing" / "staging"
    stored = store_downloaded_file(
        DownloadedFile("safe name.htm", CONTENT), destination_directory=destination
    )

    assert stored.document_name == "safe name.htm"
    assert stored.path == destination / "safe name.htm"
    assert stored.path.read_bytes() == CONTENT
    assert stored.size_bytes == len(CONTENT)
    assert stored.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert list(destination.iterdir()) == [stored.path]
    with pytest.raises(FrozenInstanceError):
        stored.size_bytes = 0  # type: ignore[misc]


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../file",
        "folder/file",
        "folder\\file",
        "a\x00b",
        "a\nb",
        "a\x7fb",
        "%2e%2e",
        "%2e%2e%2ffile",
        "a%5cb",
        "a%2fb",
        "a%00b",
    ],
)
def test_rejects_unsafe_names_before_writing(tmp_path: Path, name: str) -> None:
    destination = tmp_path / "staging"
    with pytest.raises(StorageError):
        store_downloaded_file(
            DownloadedFile(name, CONTENT), destination_directory=destination
        )
    assert not destination.exists()


@pytest.mark.parametrize("content", [b"", "text", bytearray(b"bytes"), None])
def test_rejects_invalid_content(tmp_path: Path, content: object) -> None:
    downloaded = DownloadedFile("report.htm", content)  # type: ignore[arg-type]
    with pytest.raises(StorageError, match="content"):
        store_downloaded_file(downloaded, destination_directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_existing_file_is_unchanged(tmp_path: Path) -> None:
    final_path = tmp_path / "report.htm"
    final_path.write_bytes(b"original")
    with pytest.raises(StorageError, match="already exists"):
        store_downloaded_file(
            DownloadedFile("report.htm", CONTENT), destination_directory=tmp_path
        )
    assert final_path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [final_path]


@pytest.mark.parametrize("dangling", [False, True])
def test_existing_symlink_is_unchanged(tmp_path: Path, dangling: bool) -> None:
    target = tmp_path / "target.htm"
    if not dangling:
        target.write_bytes(b"original")
    link = tmp_path / "report.htm"
    try:
        link.symlink_to(target)
    except OSError, NotImplementedError:
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(StorageError, match="already exists"):
        store_downloaded_file(
            DownloadedFile("report.htm", CONTENT), destination_directory=tmp_path
        )
    assert link.is_symlink()
    assert link.readlink() == target
    if not dangling:
        assert target.read_bytes() == b"original"


def test_promotion_failure_cleans_up_and_preserves_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = OSError("promotion failed")

    def fail_replace(source: Path, destination: Path) -> None:
        assert source.parent == destination.parent == tmp_path
        assert source.read_bytes() == CONTENT
        assert not destination.exists()
        raise failure

    monkeypatch.setattr("sec_edgar_lakehouse.filing_storage.os.replace", fail_replace)
    with pytest.raises(StorageError, match="promotion failed") as caught:
        store_downloaded_file(
            DownloadedFile("report.htm", CONTENT), destination_directory=tmp_path
        )
    assert caught.value.__cause__ is failure
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("corruption", [b"short", b"x" * len(CONTENT)])
def test_verifies_actual_stored_size_and_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: bytes
) -> None:
    real_fsync = os.fsync

    def corrupt_file(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, corruption)
        os.ftruncate(descriptor, len(corruption))
        real_fsync(descriptor)

    monkeypatch.setattr("sec_edgar_lakehouse.filing_storage.os.fsync", corrupt_file)
    with pytest.raises(StorageError, match="Stored (size|checksum)"):
        store_downloaded_file(
            DownloadedFile("report.htm", CONTENT), destination_directory=tmp_path
        )
    assert list(tmp_path.iterdir()) == []


def test_fsync_failure_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = OSError("flush failed")

    def fail_fsync(descriptor: int) -> None:
        raise failure

    monkeypatch.setattr("sec_edgar_lakehouse.filing_storage.os.fsync", fail_fsync)
    with pytest.raises(StorageError, match="flush failed") as caught:
        store_downloaded_file(
            DownloadedFile("report.htm", CONTENT), destination_directory=tmp_path
        )
    assert caught.value.__cause__ is failure
    assert list(tmp_path.iterdir()) == []


def test_rechecks_destination_before_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final_path = tmp_path / "report.htm"
    real_fsync = os.fsync

    def create_destination(descriptor: int) -> None:
        real_fsync(descriptor)
        final_path.write_bytes(b"existing")

    monkeypatch.setattr(
        "sec_edgar_lakehouse.filing_storage.os.fsync", create_destination
    )
    with pytest.raises(StorageError, match="already exists"):
        store_downloaded_file(
            DownloadedFile("report.htm", CONTENT), destination_directory=tmp_path
        )
    assert final_path.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [final_path]
