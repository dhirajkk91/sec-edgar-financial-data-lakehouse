"""Download one file from an SEC filing archive."""

import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

import httpx

from sec_edgar_lakehouse.filing_reference import FilingReference
from sec_edgar_lakehouse.filing_request import (
    RequestFailure,
    RequestPacer,
    request_index_or_document,
)


class DownloadError(Exception):
    """A filing file could not be requested or returned safely."""

    def __init__(
        self, message: str, *, attempts: int = 0, stop_run: bool = False
    ) -> None:
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
    """Download one filing file and return its bytes unchanged."""
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
