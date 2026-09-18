"""Discover submitted documents from an SEC filing index."""

import re
from dataclasses import dataclass
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup

from sec_edgar_lakehouse.filing_reference import FilingReference


class DiscoveryError(Exception):
    """The filing index could not be fetched or its documents could not be read."""


@dataclass(frozen=True, slots=True)
class FilingDocument:
    """One submitted document listed in Document Format Files."""

    sequence: str | None
    description: str | None
    document_name: str
    document_type: str | None


def discover_filing(
    reference: FilingReference,
    *,
    user_agent: str,
    client: httpx.Client | None = None,
) -> tuple[FilingDocument, ...]:
    """Fetch one filing index and return its submitted documents in table order.

    User-Agent must include an application identity and contact email.
    An injected client remains open and belongs to the caller.
    """
    _validate_user_agent(user_agent)
    # The accession prefix can belong to a filing agent, so use the filing CIK
    # for the archive directory.
    url = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{reference.cik_unpadded}/{reference.accession_compact}/"
        f"{reference.accession_number}-index.html"
    )
    if client is None:
        with httpx.Client() as owned_client:
            html = _fetch_index(owned_client, url, user_agent)
    else:
        html = _fetch_index(client, url, user_agent)
    return _parse_documents(html)


def _validate_user_agent(user_agent: str) -> None:
    if (
        not isinstance(user_agent, str)
        or not user_agent.isascii()
        or any(ord(char) < 32 or ord(char) == 127 for char in user_agent)
    ):
        raise DiscoveryError(
            "User-Agent must contain an application identity and contact email"
        )
    email = re.search(r"\b[^\s<>@]+@[^\s<>@]+\.[a-zA-Z]{2,}\b", user_agent)
    identity = user_agent[: email.start()] + user_agent[email.end() :] if email else ""
    if email is None or not re.search(r"[a-zA-Z]", identity):
        raise DiscoveryError(
            "User-Agent must contain an application identity and contact email"
        )


def _fetch_index(client: httpx.Client, url: str, user_agent: str) -> bytes:
    try:
        response = client.get(
            url,
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(30.0, connect=10.0),
            # A redirect would otherwise turn discovery into more than one request.
            follow_redirects=False,
        )
    except httpx.RequestError as exc:
        raise DiscoveryError(f"Could not fetch filing index {url}: {exc}") from exc
    if not response.is_success:
        raise DiscoveryError(
            f"Filing index request returned HTTP {response.status_code}: {url}"
        )
    return response.content


def _parse_documents(html: bytes) -> tuple[FilingDocument, ...]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", attrs={"summary": "Document Format Files"})
    if table is None:
        raise DiscoveryError("Filing index is missing the Document Format Files table")
    if table.find("td") is None:
        raise DiscoveryError("Document Format Files table contains no document rows")

    columns = [cell.get_text(strip=True).lower() for cell in table.find_all("th")]
    if "document" not in columns:
        raise DiscoveryError(
            "Document Format Files table is missing its Document column"
        )

    documents: list[FilingDocument] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        # SEC puts this package link in the same table as individual documents.
        if any(
            cell.get_text(" ", strip=True).lower() == "complete submission text file"
            for cell in cells
        ):
            continue

        values = dict(zip(columns, cells, strict=False))
        document_cell = values.get("document")
        if document_cell is None:
            raise DiscoveryError(
                "Document Format Files row is missing a document filename"
            )
        link = document_cell.find("a")
        # The link text is the filename even when its href opens the Inline XBRL viewer.
        name = (link if link is not None else document_cell).get_text(strip=True)
        if not name:
            raise DiscoveryError(
                "Document Format Files row is missing a document filename"
            )
        decoded_name = unquote(name)
        if (
            decoded_name in {".", ".."}
            or "/" in decoded_name
            or "\\" in decoded_name
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded_name)
        ):
            raise DiscoveryError(f"Unsafe document filename: {name!r}")

        text_values = {
            key: cell.get_text(" ", strip=True) or None for key, cell in values.items()
        }
        documents.append(
            FilingDocument(
                sequence=text_values.get("seq"),
                description=text_values.get("description"),
                document_name=name,
                document_type=text_values.get("type"),
            )
        )
    if not documents:
        raise DiscoveryError("Document Format Files table contains no document rows")
    return tuple(documents)
