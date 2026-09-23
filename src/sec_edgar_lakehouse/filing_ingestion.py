"""Coordinate discovery, downloads, and publication for one SEC filing."""

import json
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Literal

import httpx

from sec_edgar_lakehouse.filing_discovery import discover_filing
from sec_edgar_lakehouse.filing_download import DownloadError, download_filing_file
from sec_edgar_lakehouse.filing_inventory import build_filing_inventory
from sec_edgar_lakehouse.filing_publication import (
    PublicationError,
    PublishedFiling,
    publish_filing,
)
from sec_edgar_lakehouse.filing_reference import FilingReference


@dataclass(frozen=True, slots=True)
class DownloadFailure:
    document_name: str
    message: str


@dataclass(frozen=True, slots=True)
class IngestionResult:
    reference: FilingReference
    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    published: PublishedFiling | None
    staging_path: Path | None
    download_failure: DownloadFailure | None


def ingest_filing(
    reference: FilingReference,
    *,
    user_agent: str,
    bronze_directory: Path,
    client: httpx.Client | None = None,
    request_interval_seconds: float = 0.5,
) -> IngestionResult:
    """Download and publish one filing, stopping at the first download failure."""
    if request_interval_seconds < 0.5:
        raise ValueError("Request interval must be at least 0.5 seconds")

    if client is None:
        with httpx.Client() as owned_client:
            return _ingest_with_client(
                reference,
                user_agent,
                bronze_directory,
                owned_client,
                request_interval_seconds,
            )
    return _ingest_with_client(
        reference, user_agent, bronze_directory, client, request_interval_seconds
    )


def _ingest_with_client(
    reference: FilingReference,
    user_agent: str,
    bronze_directory: Path,
    client: httpx.Client,
    request_interval_seconds: float,
) -> IngestionResult:
    discovery = discover_filing(reference, user_agent=user_agent, client=client)
    inventory = build_filing_inventory(reference, discovery)

    contents: dict[str, bytes] = {}
    download_failure: DownloadFailure | None = None
    required_download_failed = False
    for entry in inventory.entries:
        # Discovery counts too; wait before the first document request.
        sleep(request_interval_seconds)
        try:
            downloaded = download_filing_file(
                reference,
                document_name=entry.document_name,
                user_agent=user_agent,
                client=client,
            )
        except DownloadError as exc:
            download_failure = DownloadFailure(entry.document_name, str(exc))
            required_download_failed = entry.required_for_source
            break
        contents[entry.document_name] = downloaded.content

    try:
        published = publish_filing(
            inventory, contents, discovery=discovery, bronze_directory=bronze_directory
        )
    except PublicationError as exc:
        if not required_download_failed or exc.staging_path is None:
            raise
        return IngestionResult(
            reference, "FAILED", None, exc.staging_path, download_failure
        )

    return IngestionResult(
        reference,
        _published_status(published.manifest_path),
        published,
        None,
        download_failure,
    )


def _published_status(manifest_path: Path) -> Literal["COMPLETE", "PARTIAL"]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(manifest, dict):
        status = manifest.get("status")
        if status == "COMPLETE":
            return "COMPLETE"
        if status == "PARTIAL":
            return "PARTIAL"
    raise PublicationError(f"Published manifest has invalid status: {manifest_path}")
