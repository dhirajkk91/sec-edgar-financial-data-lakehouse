"""Stage and publish one verified filing to local Bronze storage."""

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sec_edgar_lakehouse.filing_discovery import FilingDiscovery
from sec_edgar_lakehouse.filing_download import DownloadedFile
from sec_edgar_lakehouse.filing_inventory import (
    ArtifactSection,
    FilingInventory,
)
from sec_edgar_lakehouse.filing_storage import (
    StorageError,
    StoredFile,
    store_downloaded_file,
)

_SECTION_DIRECTORIES = {
    ArtifactSection.SUBMITTED: "submitted",
    ArtifactSection.SUBMISSION_PACKAGE: "submission-package",
    ArtifactSection.DATA_FILE: "sec-derived",
}


class PublicationError(Exception):
    """A filing could not be safely published."""

    def __init__(self, message: str, *, staging_path: Path | None = None) -> None:
        super().__init__(message)
        self.staging_path = staging_path


@dataclass(frozen=True, slots=True)
class PublishedFiling:
    """The canonical filing directory and its run manifest."""

    path: Path
    manifest_path: Path


def publish_filing(
    inventory: FilingInventory,
    file_contents: Mapping[str, bytes],
    *,
    discovery: FilingDiscovery,
    bronze_directory: Path,
) -> PublishedFiling:
    """Stage supplied bytes and publish only after required files are verified."""
    _validate_inputs(inventory, file_contents, discovery, bronze_directory)
    reference = inventory.reference
    bronze_directory = bronze_directory.resolve()
    final_path = (
        bronze_directory
        / f"cik={reference.cik}"
        / f"accession={reference.accession_number}"
    )
    run_id = uuid4().hex
    # A sibling staging tree keeps the final move on the same filesystem.
    staging_parent = bronze_directory.parent / ".filing-staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    if (
        staging_parent.stat().st_dev
        != _existing_ancestor(bronze_directory).stat().st_dev
    ):
        raise PublicationError(
            "Staging and canonical Bronze must be on the same filesystem"
        )
    staging_path = Path(
        tempfile.mkdtemp(
            prefix=f"{reference.cik}-{reference.accession_number}-",
            dir=staging_parent,
        )
    )
    manifest_relative_path = Path("manifests") / f"run_id={run_id}.json"
    records: list[dict[str, Any]] = []
    stored_files: dict[tuple[str, str], StoredFile] = {}
    cleanup_error: str | None = None
    metadata_error: str | None = None
    inventory_names = {entry.document_name for entry in inventory.entries}

    for entry in inventory.entries:
        record: dict[str, Any] = {
            "document_name": entry.document_name,
            "section": entry.section.value,
            "required_for_source": entry.required_for_source,
            "status": "MISSING",
        }
        records.append(record)
        if entry.document_name not in file_contents:
            continue
        try:
            stored = store_downloaded_file(
                DownloadedFile(entry.document_name, file_contents[entry.document_name]),
                destination_directory=staging_path
                / _SECTION_DIRECTORIES[entry.section],
            )
        except (StorageError, OSError) as exc:
            record["status"] = "FAILED"
            record["error"] = str(exc)
            if not entry.required_for_source:
                try:
                    _remove_failed_optional_file(
                        staging_path / _SECTION_DIRECTORIES[entry.section],
                        entry.document_name,
                        inventory_names,
                    )
                except OSError as removal_error:
                    cleanup_error = str(removal_error)
                    record["error"] += f"; cleanup failed: {removal_error}"
        else:
            stored_files[(entry.section.value, entry.document_name)] = stored

    for metadata_name in ("filing-index.html", "discovery.json"):
        record = {
            "document_name": metadata_name,
            "section": "metadata",
            "required_for_source": True,
            "status": "MISSING",
        }
        records.append(record)
        try:
            content = (
                discovery.index_content
                if metadata_name == "filing-index.html"
                else _discovery_json(inventory, discovery)
            )
            stored = store_downloaded_file(
                DownloadedFile(metadata_name, content),
                destination_directory=staging_path / "metadata",
            )
        except (StorageError, OSError, TypeError, ValueError) as exc:
            record["status"] = "FAILED"
            record["error"] = str(exc)
            if metadata_error is None:
                metadata_error = f"{metadata_name}: {exc}"
        else:
            stored_files[("metadata", metadata_name)] = stored

    for record in records:
        staged_file = stored_files.get((record["section"], record["document_name"]))
        if staged_file is None:
            continue
        try:
            _verify_staged_file(staged_file)
        except (OSError, ValueError) as exc:
            record["status"] = "FAILED"
            record["error"] = str(exc)
            if record["section"] == "metadata" and metadata_error is None:
                metadata_error = f"{record['document_name']}: {exc}"
            if not record["required_for_source"]:
                try:
                    _remove_failed_optional_file(
                        staged_file.path.parent,
                        staged_file.document_name,
                        inventory_names,
                    )
                except OSError as removal_error:
                    cleanup_error = str(removal_error)
                    record["error"] += f"; cleanup failed: {removal_error}"
        else:
            record["status"] = "VERIFIED"
            record["size_bytes"] = staged_file.size_bytes
            record["sha256"] = staged_file.sha256

    # Optional failures can publish, but every source-required file has to pass.
    required_verified = all(
        record["status"] == "VERIFIED"
        for record in records
        if record["required_for_source"]
    )
    if required_verified and cleanup_error is None:
        try:
            _require_only_verified_files(staging_path, records)
        except (OSError, PublicationError) as exc:
            cleanup_error = str(exc)

    if not required_verified or cleanup_error is not None:
        failure_error = (
            "; ".join(
                error for error in (metadata_error, cleanup_error) if error is not None
            )
            or None
        )
        _write_manifest(
            staging_path / manifest_relative_path,
            _manifest(inventory, run_id, "FAILED", False, records, error=failure_error),
        )
        detail = f": {metadata_error}" if metadata_error is not None else ""
        raise PublicationError(
            f"Filing was not published{detail}; inspect {staging_path / manifest_relative_path}",
            staging_path=staging_path,
        )

    run_status = (
        "COMPLETE"
        if all(record["status"] == "VERIFIED" for record in records)
        else "PARTIAL"
    )
    # The manifest is already in place when the filing becomes visible in Bronze.
    _write_manifest(
        staging_path / manifest_relative_path,
        _manifest(inventory, run_id, run_status, True, records),
    )
    try:
        _require_absent(final_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        _require_absent(final_path)
        os.rename(staging_path, final_path)
    except (OSError, PublicationError) as exc:
        _write_manifest(
            staging_path / manifest_relative_path,
            _manifest(inventory, run_id, "FAILED", False, records, error=str(exc)),
        )
        raise PublicationError(
            f"Could not publish filing to {final_path}: {exc}",
            staging_path=staging_path,
        ) from exc

    return PublishedFiling(final_path, final_path / manifest_relative_path)


def _validate_inputs(
    inventory: FilingInventory,
    file_contents: Mapping[str, bytes],
    discovery: FilingDiscovery,
    bronze_directory: Path,
) -> None:
    if not isinstance(inventory, FilingInventory):
        raise PublicationError("Inventory must be a FilingInventory")
    if not any(
        entry.section is ArtifactSection.SUBMITTED and entry.required_for_source
        for entry in inventory.entries
    ):
        raise PublicationError("Inventory needs at least one required submitted file")
    required_packages = sum(
        entry.section is ArtifactSection.SUBMISSION_PACKAGE
        and entry.required_for_source
        for entry in inventory.entries
    )
    if required_packages != 1:
        raise PublicationError(
            "Inventory needs exactly one required submission package"
        )
    if not isinstance(discovery, FilingDiscovery):
        raise PublicationError("Discovery evidence must be a FilingDiscovery")
    if not isinstance(bronze_directory, Path):
        raise PublicationError("Bronze directory must be a pathlib.Path")
    if not isinstance(file_contents, Mapping):
        raise PublicationError("File contents must be a mapping of names to bytes")
    inventory_names = {entry.document_name for entry in inventory.entries}
    unknown_names = set(file_contents) - inventory_names
    if unknown_names:
        raise PublicationError(f"Files not in the inventory: {sorted(unknown_names)!r}")


def _discovery_json(inventory: FilingInventory, discovery: FilingDiscovery) -> bytes:
    if (
        not isinstance(discovery.index_url, str)
        or not discovery.index_url
        or not isinstance(discovery.retrieved_at, datetime)
        or discovery.retrieved_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("Discovery evidence needs an index URL and UTC retrieval time")

    discovered = [
        (
            ArtifactSection.SUBMITTED,
            document.document_name,
            document.description,
            document.sequence,
            document.document_type,
            True,
        )
        for document in discovery.submitted_documents
    ]
    discovered.append(
        (
            ArtifactSection.SUBMISSION_PACKAGE,
            discovery.complete_submission.document_name,
            None,
            None,
            None,
            True,
        )
    )
    discovered.extend(
        (
            ArtifactSection.DATA_FILE,
            document.document_name,
            document.description,
            document.sequence,
            document.document_type,
            False,
        )
        for document in discovery.data_files
    )
    if len(discovered) != len(inventory.entries):
        raise ValueError("Discovery and inventory entries do not match")

    normalized = []
    for entry, (section, name, description, sequence, document_type, required) in zip(
        inventory.entries, discovered, strict=True
    ):
        if (
            entry.section,
            entry.document_name,
            entry.sequence,
            entry.document_type,
            entry.required_for_source,
        ) != (section, name, sequence, document_type, required):
            raise ValueError("Discovery and inventory entries do not match")
        normalized.append(
            {
                "section": entry.section.value,
                "document_name": entry.document_name,
                "description": description,
                "sequence": entry.sequence,
                "document_type": entry.document_type,
                "required_for_source": entry.required_for_source,
            }
        )

    record = {
        "cik": inventory.reference.cik,
        "accession_number": inventory.reference.accession_number,
        "index_url": discovery.index_url,
        "retrieved_at": discovery.retrieved_at.isoformat(),
        "inventory": normalized,
    }
    return (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _verify_staged_file(stored: StoredFile) -> None:
    if stored.path.is_symlink():
        raise ValueError(f"Staged file is a symlink: {stored.path}")
    content = stored.path.read_bytes()
    if len(content) != stored.size_bytes:
        raise ValueError(f"Staged size changed: {stored.path}")
    if hashlib.sha256(content).hexdigest() != stored.sha256:
        raise ValueError(f"Staged checksum changed: {stored.path}")


def _remove_failed_optional_file(
    directory: Path, document_name: str, inventory_names: set[str]
) -> None:
    if directory.is_symlink():
        raise OSError(f"Cannot safely inspect symlinked directory {directory}")
    if not directory.exists():
        return
    # A different inventory filename can look like our temp-file prefix.
    temporary_prefix = f".{document_name}."
    for path in directory.iterdir():
        if path.name != document_name and path.name in inventory_names:
            continue
        if path.name == document_name or (
            path.name.startswith(temporary_prefix) and path.name.endswith(".tmp")
        ):
            if path.is_dir() and not path.is_symlink():
                raise OSError(f"Cannot safely remove directory {path}")
            path.unlink()


def _require_only_verified_files(
    staging_path: Path, records: list[dict[str, Any]]
) -> None:
    verified_paths = {
        staging_path
        / (
            "metadata"
            if record["section"] == "metadata"
            else _SECTION_DIRECTORIES[ArtifactSection(record["section"])]
        )
        / record["document_name"]
        for record in records
        if record["status"] == "VERIFIED"
    }
    actual_paths: set[Path] = set()
    for path in staging_path.iterdir():
        if (
            path.name not in (*_SECTION_DIRECTORIES.values(), "metadata")
            or not path.is_dir()
            or path.is_symlink()
        ):
            raise PublicationError(f"Unexpected staged path remains: {path}")
    for directory_name in (*_SECTION_DIRECTORIES.values(), "metadata"):
        directory = staging_path / directory_name
        if directory.exists():
            for path in directory.iterdir():
                if (
                    path not in verified_paths
                    or not path.is_file()
                    or path.is_symlink()
                ):
                    raise PublicationError(f"Unverified staged file remains: {path}")
                actual_paths.add(path)
    if actual_paths != verified_paths:
        raise PublicationError(
            f"Verified staged files are missing: {sorted(verified_paths - actual_paths)!r}"
        )


def _require_absent(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise PublicationError(f"Canonical filing already exists: {path}")


def _existing_ancestor(path: Path) -> Path:
    while not path.exists():
        path = path.parent
    return path


def _manifest(
    inventory: FilingInventory,
    run_id: str,
    status: str,
    source_complete: bool,
    records: list[dict[str, Any]],
    *,
    error: str | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "source_complete": source_complete,
        "cik": inventory.reference.cik,
        "accession_number": inventory.reference.accession_number,
        "files": records,
    }
    if error is not None:
        manifest["error"] = error
    return manifest


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".manifest-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(manifest, temporary_file, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise PublicationError(f"Could not write run manifest {path}: {exc}") from exc
