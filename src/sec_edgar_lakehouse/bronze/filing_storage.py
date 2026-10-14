"""A download only counts as stored after we read the saved bytes back.

Each file is written beside its destination through a temporary file.
Existing files are refused, and failures clean up only the temporary
file created by that attempt.
"""

import hashlib
import ntpath
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from sec_edgar_lakehouse.bronze.filing_download import DownloadedFile


class StorageError(Exception):
    """A downloaded file could not be safely stored."""


@dataclass(frozen=True, slots=True)
class StoredFile:
    """A stored file and its verified size and checksum."""

    document_name: str
    path: Path
    size_bytes: int
    sha256: str


def store_downloaded_file(
    downloaded: DownloadedFile, *, destination_directory: Path
) -> StoredFile:
    """Save through a temporary file, then check the bytes before returning its path.

    The folder may be created, but an existing destination file is an error.
    If saving fails, only this attempt's temporary file is cleaned up.
    StorageError is raised instead of returning an unchecked file.
    """
    _validate_downloaded_file(downloaded)
    if not isinstance(destination_directory, Path):
        raise StorageError("Destination directory must be a pathlib.Path")

    final_path = destination_directory / downloaded.document_name
    temporary_path: Path | None = None
    try:
        destination_directory.mkdir(parents=True, exist_ok=True)
        _require_absent(final_path)
        # Keep both files on the same filesystem for atomic promotion.
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination_directory,
            prefix=f".{downloaded.document_name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(downloaded.content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        # Windows needs the temp file closed; check what actually landed on disk.
        size_bytes = temporary_path.stat().st_size
        if size_bytes != len(downloaded.content):
            raise StorageError(f"Stored size does not match content: {temporary_path}")
        checksum = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        if checksum != hashlib.sha256(downloaded.content).hexdigest():
            raise StorageError(
                f"Stored checksum does not match content: {temporary_path}"
            )

        _require_absent(final_path)
        # Recheck just before promotion so a file created during our write is refused.
        os.replace(temporary_path, final_path)
        return StoredFile(downloaded.document_name, final_path, size_bytes, checksum)
    except Exception as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise StorageError(
                    f"Could not store {final_path}: {exc}; could not remove temporary "
                    f"file {temporary_path}: {cleanup_error}"
                ) from exc
        if isinstance(exc, StorageError):
            raise
        raise StorageError(f"Could not store {final_path}: {exc}") from exc


def _require_absent(path: Path) -> None:
    """Refuse existing files and symlinks rather than overwriting a previous download."""
    if path.exists() or path.is_symlink():
        raise StorageError(f"Destination already exists: {path}")


def _validate_downloaded_file(downloaded: DownloadedFile) -> None:
    """Require nonempty bytes and a name that stays a single safe local filename."""
    name = downloaded.document_name
    if not isinstance(name, str) or not name:
        raise StorageError("Document name must not be empty")
    decoded_name = unquote(name)
    if (
        decoded_name in {".", ".."}
        or "/" in decoded_name
        or "\\" in decoded_name
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded_name)
        or Path(name).name != name
        or (os.name == "nt" and ntpath.isreserved(name))
    ):
        raise StorageError(f"Unsafe document name: {name!r}")
    if not isinstance(downloaded.content, bytes):
        raise StorageError("Downloaded content must be bytes")
    if not downloaded.content:
        raise StorageError("Downloaded content must not be empty")
