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
    # Keep '?' and '#' in the filename, not in the URL's query or fragment.
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
    if block_reason := _sec_access_block_reason(response.content):
        raise DownloadError(f"{block_reason}: {url}")
    if length_error := _content_length_error(response):
        raise DownloadError(f"{length_error}: {url}")
    return response.content


def _sec_access_block_reason(content: bytes) -> str | None:
    preview = content[:8192].decode("utf-8", errors="ignore").casefold()
    if "<html" not in preview[:512]:
        return None
    title = re.search(r"<title\b[^>]*>(.*?)</title>", preview, flags=re.DOTALL)
    if title is None:
        return None
    title_text = re.sub(r"<[^>]+>", "", title.group(1)).strip()
    if "sec.gov" in title_text and "request rate threshold exceeded" in title_text:
        return "SEC rate-limit page returned with HTTP 200"
    if "sec.gov" in title_text and (
        "undeclared automated tool" in title_text or "access denied" in title_text
    ):
        return "SEC access-block page returned with HTTP 200"
    if (
        title_text == "access denied"
        and "sec.gov" in preview
        and ("reference #" in preview or "permission to access" in preview)
    ):
        return "SEC access-block page returned with HTTP 200"
    return None


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
