"""Coordinate discovery, downloads, and publication for one SEC filing."""

import json
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Literal

import httpx

from sec_edgar_lakehouse.filing_discovery import discover_filing
from sec_edgar_lakehouse.filing_download import DownloadError, download_filing_file
from sec_edgar_lakehouse.filing_inventory import build_filing_inventory
from sec_edgar_lakehouse.filing_publication import (
    PublicationError,
    PublishedFiling,
    _primary_document_name,
    _validate_staged_content,
    publish_filing,
)
from sec_edgar_lakehouse.filing_reference import FilingReference
from sec_edgar_lakehouse.filing_request import RequestPacer


@dataclass(frozen=True, slots=True)
class DownloadFailure:
    document_name: str
    message: str
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class IngestionResult:
    reference: FilingReference
    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    published: PublishedFiling | None
    staging_path: Path | None
    download_failure: DownloadFailure | None
    parser_ready: bool


def ingest_filing(
    reference: FilingReference,
    *,
    user_agent: str,
    bronze_directory: Path,
    client: httpx.Client | None = None,
    request_interval_seconds: float = 0.5,
) -> IngestionResult:
    """Download in inventory order and publish verified required files."""
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
    pacer = RequestPacer(request_interval_seconds, clock=monotonic, sleeper=sleep)
    discovery = discover_filing(
        reference, user_agent=user_agent, client=client, _pacer=pacer
    )
    inventory = build_filing_inventory(reference, discovery)
    primary_name = _primary_document_name(discovery)

    contents: dict[str, bytes] = {}
    failed_files: dict[str, str] = {}
    network_attempts: dict[str, int] = {}
    download_failure: DownloadFailure | None = None
    required_download_failed = False
    stop_run_error: str | None = None
    for entry in inventory.entries:
        try:
            downloaded = download_filing_file(
                reference,
                document_name=entry.document_name,
                user_agent=user_agent,
                client=client,
                _pacer=pacer,
            )
        except DownloadError as exc:
            message = str(exc)
            attempts = exc.attempts
            stop_run_error = message if exc.stop_run else None
        else:
            attempts = downloaded.attempts
            try:
                # Check content now so a failed required file stops later requests.
                # Publication still checks the staged bytes after writing them.
                _validate_staged_content(entry, downloaded.content, primary_name)
            except ValueError as exc:
                message = str(exc)
            else:
                contents[entry.document_name] = downloaded.content
                network_attempts[entry.document_name] = attempts
                continue
        failed_files[entry.document_name] = message
        if attempts:
            network_attempts[entry.document_name] = attempts
        failure = DownloadFailure(entry.document_name, message, attempts)
        if download_failure is None or entry.required_for_source or stop_run_error:
            download_failure = failure
        if entry.required_for_source or stop_run_error:
            required_download_failed = True
            break

    try:
        published = publish_filing(
            inventory,
            contents,
            discovery=discovery,
            bronze_directory=bronze_directory,
            failed_files=failed_files,
            network_attempts=network_attempts,
            stop_run_error=stop_run_error,
        )
    except PublicationError as exc:
        if not required_download_failed or exc.staging_path is None:
            raise
        return IngestionResult(
            reference,
            "FAILED",
            None,
            exc.staging_path,
            download_failure,
            exc.parser_ready,
        )

    status, parser_ready = _published_outcome(published.manifest_path)
    return IngestionResult(
        reference,
        status,
        published,
        None,
        download_failure,
        parser_ready,
    )


def _published_outcome(
    manifest_path: Path,
) -> tuple[Literal["COMPLETE", "PARTIAL"], bool]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(manifest, dict):
        status = manifest.get("status")
        parser_ready = manifest.get("parser_ready")
        if isinstance(parser_ready, bool):
            if status == "COMPLETE":
                return "COMPLETE", parser_ready
            if status == "PARTIAL":
                return "PARTIAL", parser_ready
    raise PublicationError(f"Published manifest has invalid status: {manifest_path}")
