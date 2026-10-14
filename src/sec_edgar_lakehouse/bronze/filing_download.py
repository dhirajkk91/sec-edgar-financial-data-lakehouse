"""This is the download step between the filing index and Bronze storage.

A successful HTTP response is not enough: empty bodies, bad lengths and
SEC block pages still fail. Accepted bytes stay in memory until storage
saves and verifies them.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

import httpx

from sec_edgar_lakehouse.bronze.filing_request import (
    RequestFailure,
    RequestPacer,
    request_index_or_document,
)
from sec_edgar_lakehouse.filing_reference import FilingReference


class DownloadError(Exception):
    """A filing file could not be requested or returned safely."""

    def __init__(
        self, message: str, *, attempts: int = 0, stop_run: bool = False
    ) -> None:
        """Keep the request attempt count and SEC stop signal available to ingestion."""
        super().__init__(message)
        self.attempts = attempts
        self.stop_run = stop_run


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    """One downloaded filing file held in memory."""

    document_name: str
    content: bytes
    attempts: int = field(default=0, compare=False)


def download_filing_file(
    reference: FilingReference,
    *,
    document_name: str,
    user_agent: str,
    client: httpx.Client | None = None,
    _pacer: RequestPacer | None = None,
) -> DownloadedFile:
    """Download one safe archive filename and check its response length.

    The User-Agent must identify the caller. A supplied client stays open.
    DownloadError includes the attempt count and whether this SEC response
    should stop requests for the rest of the company run.
    """
    _validate_user_agent(user_agent)
    _validate_document_name(document_name)
    # Keep '?' and '#' in the filename, not in the URL's query or fragment.
    encoded_document_name = quote(document_name, safe="._-")

    # The accession prefix can name a filing agent, so the archive uses the filing CIK.
    url = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{reference.cik_unpadded}/{reference.accession_compact}/{encoded_document_name}"
    )
    if client is None:
        with httpx.Client() as owned_client:
            content, attempts = _fetch_file(owned_client, url, user_agent, _pacer)
    else:
        content, attempts = _fetch_file(client, url, user_agent, _pacer)

    return DownloadedFile(
        document_name=document_name, content=content, attempts=attempts
    )


def _validate_user_agent(user_agent: str) -> None:
    """Reject missing contact details or header control characters before making a request."""
    if (
        not isinstance(user_agent, str)
        or not user_agent.isascii()
        or any(ord(char) < 32 or ord(char) == 127 for char in user_agent)
    ):
        raise DownloadError(
            "User-Agent must contain an application identity and contact email"
        )
    email = re.search(r"\b[^\s<>@]+@[^\s<>@]+\.[a-zA-Z]{2,}\b", user_agent)
    identity = user_agent[: email.start()] + user_agent[email.end() :] if email else ""
    if email is None or not re.search(r"[a-zA-Z]", identity):
        raise DownloadError(
            "User-Agent must contain an application identity and contact email"
        )


def _validate_document_name(document_name: str) -> None:
    """Treat the document as one filename, including after URL decoding."""
    if not isinstance(document_name, str) or not document_name:
        raise DownloadError("Document name must not be empty")

    decoded_name = unquote(document_name)
    if (
        decoded_name in {".", ".."}
        or "/" in decoded_name
        or "\\" in decoded_name
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded_name)
    ):
        raise DownloadError(f"Unsafe document name: {document_name!r}")


def _fetch_file(
    client: httpx.Client, url: str, user_agent: str, pacer: RequestPacer | None
) -> tuple[bytes, int]:
    """Turn request failures, empty responses and bad lengths into download errors."""
    try:
        requested = request_index_or_document(client, url, user_agent, pacer=pacer)
    except RequestFailure as exc:
        if exc.attempts == 1 and str(exc).startswith("HTTP ") and not exc.stop_run:
            message = f"Filing file request returned {exc}: {url}"
        else:
            message = f"Filing file request failed after {exc.attempts} attempts: {exc}: {url}"
        raise DownloadError(
            message, attempts=exc.attempts, stop_run=exc.stop_run
        ) from exc
    response = requested.response
    if not response.content:
        raise DownloadError(
            f"Filing file response was empty: {url}", attempts=requested.attempts
        )
    if length_error := _content_length_error(response):
        raise DownloadError(f"{length_error}: {url}", attempts=requested.attempts)
    return response.content, requested.attempts


def _content_length_error(response: httpx.Response) -> str | None:
    """Check uncompressed lengths only; httpx has already unpacked compressed bodies."""
    # The header can describe compressed wire bytes, while httpx gives us the decoded body.
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        return None
    header = response.headers.get("Content-Length")
    if header is None:
        return None
    try:
        expected = int(header)
    except ValueError:
        return "Invalid Content-Length header"
    if expected != len(response.content):
        return f"Content-Length {expected} does not match {len(response.content)} response bytes"
    return None
