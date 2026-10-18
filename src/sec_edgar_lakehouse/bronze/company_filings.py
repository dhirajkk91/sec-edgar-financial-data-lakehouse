"""Company runs start with the SEC submissions listing.

We keep the response bytes alongside the parsed recent filings so later
metadata loads can check the same source. Selection uses filing dates,
not the dates of the financial periods inside a filing.
"""

import json
import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import unquote

import httpx

from sec_edgar_lakehouse.bronze import filing_request
from sec_edgar_lakehouse.bronze.filing_discovery import (
    DiscoveryError,
    _fetch_content,
    _validate_user_agent,
)
from sec_edgar_lakehouse.filing_reference import FilingReference, _normalize_cik


class CompanyFilingsDiscoveryError(Exception):
    """Company discovery or selection received unusable input or an SEC response."""

    def __init__(self, message: str, *, stop_run: bool = False) -> None:
        super().__init__(message)
        self.stop_run = stop_run


@dataclass(frozen=True, slots=True)
class CompanyFiling:
    """One filing from the company listing, including its IDs and report date."""

    reference: FilingReference
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    source_listing_name: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryFileReference:
    """A history file named by the company's main SEC submissions response."""

    name: str
    filing_count: int
    filing_from: date
    filing_to: date


@dataclass(frozen=True, slots=True)
class HistoryListing:
    """Verified older listing bytes and the filings they supplied."""

    reference: HistoryFileReference
    filings: tuple[CompanyFiling, ...]
    url: str
    retrieved_at: datetime
    content: bytes = field(repr=False)
    range_mismatches: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompanyFilingsDiscovery:
    """The recent filings plus the original response bytes that back them up."""

    cik: str
    entity_name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    recent_filings: tuple[CompanyFiling, ...]
    submissions_url: str
    retrieved_at: datetime
    submissions_content: bytes = field(repr=False)
    history_files: tuple[HistoryFileReference, ...] = ()
    history_listings: tuple[HistoryListing, ...] = ()

    @property
    def all_filings(self) -> tuple[CompanyFiling, ...]:
        """Use the recent copy of an identical overlap; conflicts fail during discovery."""
        seen = {filing.reference.accession_number for filing in self.recent_filings}
        older = []
        for listing in self.history_listings:
            for filing in listing.filings:
                if filing.reference.accession_number not in seen:
                    older.append(filing)
                    seen.add(filing.reference.accession_number)
        return self.recent_filings + tuple(older)


def discover_company_filings(
    cik: str,
    *,
    user_agent: str,
    client: httpx.Client | None = None,
    _pacer: filing_request.RequestPacer | None = None,
    include_history: bool = False,
    history_since: date | None = None,
) -> CompanyFilingsDiscovery:
    """Fetch the company listing and keep the response exactly as received.

    Older listing files are fetched only when include_history is true.
    A supplied HTTP client stays open for its caller. Bad inputs or an invalid
    response raise CompanyFilingsDiscoveryError. Nothing is written to disk.
    """
    try:
        normalized = _normalize_cik(cik)
        _validate_user_agent(user_agent)
        url = f"https://data.sec.gov/submissions/CIK{normalized}.json"
        pacer = (
            filing_request.RequestPacer(0.5, sleeper=filing_request.sleep)
            if _pacer is None
            else _pacer
        )
        if not isinstance(include_history, bool):
            raise TypeError("include_history must be a boolean")
        if history_since is not None and type(history_since) is not date:
            raise TypeError("history_since must be a date")
        if client is None:
            with httpx.Client() as owned:
                return _discover_with_client(
                    normalized,
                    url,
                    user_agent,
                    owned,
                    pacer,
                    include_history,
                    history_since,
                )
        return _discover_with_client(
            normalized, url, user_agent, client, pacer, include_history, history_since
        )
    except (TypeError, ValueError, DiscoveryError) as exc:
        raise CompanyFilingsDiscoveryError(
            str(exc), stop_run=isinstance(exc, DiscoveryError) and exc.stop_run
        ) from exc


def _discover_with_client(
    cik: str,
    url: str,
    user_agent: str,
    client: httpx.Client,
    pacer: filing_request.RequestPacer,
    include_history: bool,
    history_since: date | None,
) -> CompanyFilingsDiscovery:
    content, _ = _fetch_content(
        client, url, user_agent, pacer, subject="Company submissions"
    )
    discovery = _parse(
        content, cik, url, datetime.now(UTC), include_history=include_history
    )
    if not include_history:
        return discovery
    listings: list[HistoryListing] = []
    seen = {
        filing.reference.accession_number: filing for filing in discovery.recent_filings
    }
    for reference in discovery.history_files:
        if history_since is not None and reference.filing_to < history_since:
            continue
        history_url = f"https://data.sec.gov/submissions/{reference.name}"
        history_content, _ = _fetch_content(
            client,
            history_url,
            user_agent,
            pacer,
            subject="Company submissions history",
        )
        listing = _parse_history(
            history_content,
            cik,
            reference,
            history_url,
            datetime.now(UTC),
            history_since=history_since,
        )
        for filing in listing.filings:
            accession = filing.reference.accession_number
            earlier = seen.get(accession)
            if earlier is not None and (
                earlier.form,
                earlier.filing_date,
                earlier.report_date,
                earlier.primary_document,
            ) != (
                filing.form,
                filing.filing_date,
                filing.report_date,
                filing.primary_document,
            ):
                raise ValueError(
                    f"Conflicting accession across submissions listings: {accession}"
                )
            seen[accession] = filing
        listings.append(listing)
    return replace(discovery, history_listings=tuple(listings))


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject repeated JSON keys so a later value cannot silently replace an earlier one."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    """NaN and Infinity are not valid source JSON, even though Python accepts them by default."""
    raise ValueError(f"Invalid JSON constant: {value}")


def _text(value: object, name: str) -> str:
    """Required text cannot be blank; keep its original spelling once it passes."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    """Optional company lists still need to be arrays of strings, not silently coerced values."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    return tuple(value)


def _date(value: object, name: str) -> date:
    """The listing uses calendar dates in YYYY-MM-DD; a timestamp is not interchangeable."""
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
    ):
        raise ValueError(f"{name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}: {value!r}") from exc


def _document_path(value: object) -> str:
    """Check encoded path parts for unsafe names, keeping the SEC spelling unchanged."""
    original = _text(value, "primaryDocument")
    decoded = original
    while True:
        if (
            decoded.startswith("/")
            or "\\" in decoded
            or PureWindowsPath(decoded).drive
            or "?" in decoded
            or "#" in decoded
            or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", decoded) is not None
            or any(unicodedata.category(char) == "Cc" for char in decoded)
        ):
            raise ValueError(f"Unsafe primaryDocument path: {original!r}")
        segments = decoded.split("/")
        if any(not segment or segment in {".", ".."} for segment in segments):
            raise ValueError(f"Unsafe primaryDocument path: {original!r}")
        next_value = unquote(decoded)
        if next_value == decoded:
            return original
        # SEC values stay unchanged; decoding is only for catching hidden traversal.
        decoded = next_value


def _parse(
    content: bytes,
    cik: str,
    url: str,
    retrieved_at: datetime,
    *,
    include_history: bool = False,
) -> CompanyFilingsDiscovery:
    """Check the company ID and matching array lengths, then read the recent filings."""
    data = json.loads(
        content, object_pairs_hook=_object, parse_constant=_invalid_constant
    )
    if not isinstance(data, dict):
        raise TypeError("Submissions JSON must be an object")
    returned_cik = data.get("cik")
    if type(returned_cik) is int:
        returned_cik = str(returned_cik)
    if not isinstance(returned_cik, str):
        raise TypeError("Returned CIK must be a string or integer")
    if _normalize_cik(returned_cik) != cik:
        raise ValueError("Requested and returned CIK do not match")
    name = _text(data.get("name"), "Entity name")
    tickers = _strings(data.get("tickers"), "tickers")
    exchanges = _strings(data.get("exchanges"), "exchanges")
    if len(tickers) != len(exchanges):
        raise ValueError("tickers and exchanges must have matching lengths")
    filings = data.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise TypeError("Missing or invalid filings.recent")
    assert isinstance(filings, dict)
    rows = _parse_rows(recent, cik, "filings.recent", None)
    history_files = (
        _parse_history_references(filings.get("files", []), cik)
        if include_history
        else ()
    )
    return CompanyFilingsDiscovery(
        cik,
        name,
        tickers,
        exchanges,
        rows,
        url,
        retrieved_at,
        content,
        history_files,
    )


def _parse_history_references(
    value: object, cik: str
) -> tuple[HistoryFileReference, ...]:
    if not isinstance(value, list):
        raise TypeError("filings.files must be an array")
    references = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("Each filings.files entry must be an object")
        name = _text(item.get("name"), "History file name")
        if (
            re.fullmatch(rf"CIK{cik}-submissions-[0-9]{{3}}\.json", name) is None
            or name in seen
        ):
            raise ValueError(f"Invalid or duplicate history file name: {name!r}")
        seen.add(name)
        count = item.get("filingCount")
        if type(count) is not int or count < 0:
            raise ValueError("History filingCount must be a non-negative integer")
        first = _date(item.get("filingFrom"), "filingFrom")
        last = _date(item.get("filingTo"), "filingTo")
        if first > last:
            raise ValueError("History filingFrom must not be after filingTo")
        references.append(HistoryFileReference(name, count, first, last))
    return tuple(references)


def _parse_rows(
    data: dict[str, Any],
    cik: str,
    location: str,
    source_listing_name: str | None,
    history_since: date | None = None,
) -> tuple[CompanyFiling, ...]:
    columns = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    for column in columns:
        if not isinstance(data.get(column), list):
            raise TypeError(f"{location}.{column} must be an array")
    # Unequal parallel arrays could attach another filing's date or document to an accession.
    if len({len(data[column]) for column in columns}) != 1:
        raise ValueError(f"Required {location} columns must have equal lengths")
    rows = []
    seen = set()
    for accession, filed, reported, form, document in zip(
        *(data[c] for c in columns), strict=True
    ):
        reference = FilingReference(cik, accession)
        if accession in seen:
            raise ValueError(f"Duplicate accession number: {accession}")
        seen.add(accession)
        filing_date = _date(filed, "filingDate")
        report_date = None if reported == "" else _date(reported, "reportDate")
        filing_form = _text(form, "form")
        # Old SEC rows can have no primary document. They cannot be selected,
        # but a later, in-scope financial row still needs a safe document path.
        if (
            document == ""
            and source_listing_name is not None
            and (
                filing_form not in {"10-K", "10-Q"}
                or (history_since is not None and filing_date < history_since)
            )
        ):
            continue
        primary_document = _document_path(document)
        if history_since is not None and filing_date < history_since:
            continue
        rows.append(
            CompanyFiling(
                reference,
                filing_form,
                filing_date,
                report_date,
                primary_document,
                source_listing_name,
            )
        )
    return tuple(rows)


def _parse_history(
    content: bytes,
    cik: str,
    reference: HistoryFileReference,
    url: str,
    retrieved_at: datetime,
    *,
    history_since: date | None = None,
) -> HistoryListing:
    """Bind an older response to its canonical manifest entry and validate every row."""
    data = json.loads(
        content, object_pairs_hook=_object, parse_constant=_invalid_constant
    )
    if not isinstance(data, dict):
        raise TypeError("History submissions JSON must be an object")
    if "cik" in data and _normalize_cik(data["cik"]) != cik:
        raise ValueError("Requested and returned history CIK do not match")
    rows = _parse_rows(data, cik, reference.name, reference.name, history_since)
    if len(data["accessionNumber"]) != reference.filing_count:
        raise ValueError(
            f"History filing count differs from manifest: {reference.name}"
        )
    all_dates = tuple(_date(value, "filingDate") for value in data["filingDate"])
    mismatches = []
    for accession, filed in zip(data["accessionNumber"], all_dates, strict=True):
        if filed < reference.filing_from or filed > reference.filing_to:
            mismatches.append(str(accession))
    if url != f"https://data.sec.gov/submissions/{reference.name}":
        raise ValueError("History URL must match the named SEC listing")
    return HistoryListing(
        reference, rows, url, retrieved_at, content, tuple(mismatches)
    )


def select_company_filings(
    discovery: CompanyFilingsDiscovery,
    *,
    forms: Collection[str],
    filed_on_or_after: date | None = None,
    filed_on_or_before: date | None = None,
    limit: int | None = None,
) -> tuple[CompanyFiling, ...]:
    """Choose exact form and filing-date matches, newest first, then apply the limit.

    Both date boundaries are included. An equal filing date is sorted by
    accession, descending. Report dates and amendments do not affect this sort.
    """
    if not isinstance(discovery, CompanyFilingsDiscovery):
        raise CompanyFilingsDiscoveryError("discovery must be CompanyFilingsDiscovery")
    _validate_selection_inputs(forms, filed_on_or_after, filed_on_or_before, limit)
    matches = (
        f
        for f in discovery.all_filings
        if f.form in forms
        and (filed_on_or_after is None or f.filing_date >= filed_on_or_after)
        and (filed_on_or_before is None or f.filing_date <= filed_on_or_before)
    )
    # Sort before limiting so the selected filings do not depend on the SEC listing order.
    return tuple(
        sorted(
            matches,
            key=lambda f: (f.filing_date, f.reference.accession_number),
            reverse=True,
        )[:limit]
    )


def _validate_selection_inputs(
    forms: Collection[str],
    filed_on_or_after: date | None,
    filed_on_or_before: date | None,
    limit: int | None,
) -> None:
    """Catch invalid filters before they can trigger a company-listing request."""
    if (
        isinstance(forms, (str, bytes))
        or not isinstance(forms, Collection)
        or not forms
        or any(not isinstance(f, str) or not f.strip() for f in forms)
    ):
        raise CompanyFilingsDiscoveryError(
            "forms must be a non-empty collection of non-empty strings"
        )
    for boundary in (filed_on_or_after, filed_on_or_before):
        if boundary is not None and type(boundary) is not date:
            raise CompanyFilingsDiscoveryError(
                "Filing-date boundaries must be date values"
            )
    if (
        filed_on_or_after is not None
        and filed_on_or_before is not None
        and filed_on_or_after > filed_on_or_before
    ):
        raise CompanyFilingsDiscoveryError("Filing-date start must not be after end")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise CompanyFilingsDiscoveryError("limit must be a positive integer")
