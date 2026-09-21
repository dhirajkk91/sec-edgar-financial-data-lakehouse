"""Download one file from an SEC filing archive."""

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

import httpx

from sec_edgar_lakehouse.filing_reference import FilingReference


class DownloadError(Exception):
    """A filing file could not be requested or returned safely."""


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    """One downloaded filing file held in memory."""

    document_name: str
    content: bytes


def download_filing_file(
    reference: FilingReference,
    *,
    document_name: str,
    user_agent: str,
    client: httpx.Client | None = None,
) -> DownloadedFile:
    """Download one filing file and return its bytes unchanged."""
    _validate_user_agent(user_agent)
    _validate_document_name(document_name)
    encoded_document_name = quote(document_name, safe="._-")

    # The accession prefix can name a filing agent, so the archive uses the filing CIK.
    url = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{reference.cik_unpadded}/{reference.accession_compact}/{encoded_document_name}"
    )
    if client is None:
        with httpx.Client() as owned_client:
            content = _fetch_file(owned_client, url, user_agent)
    else:
        content = _fetch_file(client, url, user_agent)

    return DownloadedFile(document_name=document_name, content=content)


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


def _fetch_file(client: httpx.Client, url: str, user_agent: str) -> bytes:
    try:
        response = client.get(
            url,
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(30.0, connect=10.0),
            # Following a redirect would silently turn this into more than one request.
            follow_redirects=False,
        )
    except httpx.RequestError as exc:
        raise DownloadError(f"Could not download filing file {url}: {exc}") from exc
    if not response.is_success:
        raise DownloadError(
            f"Filing file request returned HTTP {response.status_code}: {url}"
        )
    if not response.content:
        raise DownloadError(f"Filing file response was empty: {url}")
    return response.content
