"""Discover recent SEC company filings and select them without side effects."""

import json
import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import unquote

import httpx

from sec_edgar_lakehouse import filing_request
from sec_edgar_lakehouse.filing_discovery import (
    DiscoveryError,
    _fetch_content,
    _validate_user_agent,
)
from sec_edgar_lakehouse.filing_reference import FilingReference, _normalize_cik


class CompanyFilingsDiscoveryError(Exception):
    """Company discovery or selection received unusable input or an SEC response."""


@dataclass(frozen=True, slots=True)
class CompanyFiling:
    reference: FilingReference
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str


@dataclass(frozen=True, slots=True)
class CompanyFilingsDiscovery:
    cik: str
    entity_name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    recent_filings: tuple[CompanyFiling, ...]
    submissions_url: str
    retrieved_at: datetime
    submissions_content: bytes = field(repr=False)


def discover_company_filings(
    cik: str,
    *,
    user_agent: str,
    client: httpx.Client | None = None,
) -> CompanyFilingsDiscovery:
    """Fetch only the main submissions JSON; an injected client remains caller-owned."""
    try:
        normalized = _normalize_cik(cik)
        _validate_user_agent(user_agent)
        url = f"https://data.sec.gov/submissions/CIK{normalized}.json"
        pacer = filing_request.RequestPacer(0.5, sleeper=filing_request.sleep)
        if client is None:
            with httpx.Client() as owned:
                content, _ = _fetch_content(
                    owned, url, user_agent, pacer, subject="Company submissions"
                )
        else:
            content, _ = _fetch_content(
                client, url, user_agent, pacer, subject="Company submissions"
            )
        retrieved_at = datetime.now(UTC)
        return _parse(content, normalized, url, retrieved_at)
    except (TypeError, ValueError, DiscoveryError) as exc:
        raise CompanyFilingsDiscoveryError(str(exc)) from exc


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError(f"Invalid JSON constant: {value}")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    return tuple(value)


def _date(value: object, name: str) -> date:
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
    content: bytes, cik: str, url: str, retrieved_at: datetime
) -> CompanyFilingsDiscovery:
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
    columns = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    for column in columns:
        if not isinstance(recent.get(column), list):
            raise TypeError(f"filings.recent.{column} must be an array")
    if len({len(recent[column]) for column in columns}) != 1:
        raise ValueError("Required recent columns must have equal lengths")
    rows = []
    seen = set()
    for accession, filed, reported, form, document in zip(
        *(recent[c] for c in columns), strict=True
    ):
        reference = FilingReference(cik, accession)
        if accession in seen:
            raise ValueError(f"Duplicate accession number: {accession}")
        seen.add(accession)
        rows.append(
            CompanyFiling(
                reference,
                _text(form, "form"),
                _date(filed, "filingDate"),
                None if reported == "" else _date(reported, "reportDate"),
                _document_path(document),
            )
        )
    return CompanyFilingsDiscovery(
        cik, name, tickers, exchanges, tuple(rows), url, retrieved_at, content
    )


def select_company_filings(
    discovery: CompanyFilingsDiscovery,
    *,
    forms: Collection[str],
    filed_on_or_after: date | None = None,
    filed_on_or_before: date | None = None,
    limit: int | None = None,
) -> tuple[CompanyFiling, ...]:
    """Filter exact forms and inclusive filing dates, newest first, then apply limit."""
    if not isinstance(discovery, CompanyFilingsDiscovery):
        raise CompanyFilingsDiscoveryError("discovery must be CompanyFilingsDiscovery")
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
    matches = (
        f
        for f in discovery.recent_filings
        if f.form in forms
        and (filed_on_or_after is None or f.filing_date >= filed_on_or_after)
        and (filed_on_or_before is None or f.filing_date <= filed_on_or_before)
    )
    return tuple(
        sorted(
            matches,
            key=lambda f: (f.filing_date, f.reference.accession_number),
            reverse=True,
        )[:limit]
    )
