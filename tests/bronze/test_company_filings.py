"""The company listing is fetched through a mock HTTP transport.

The sample has tied filing dates, a nonfinancial form and a link to older
submissions. That lets us check selection order and the recent-only limit
without relying on what the SEC happens to return today.
"""

import builtins
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from sec_edgar_lakehouse import (
    CompanyFilingsDiscovery,
    CompanyFilingsDiscoveryError,
    discover_company_filings,
    select_company_filings,
)
from sec_edgar_lakehouse.bronze import company_filings, filing_request
from tests.bronze.test_filing_request import ControlledTime

USER_AGENT = "CompanyDiscovery contact@example.org"
URL = "https://data.sec.gov/submissions/CIK0001122304.json"


def payload() -> dict[str, Any]:
    """Tied dates and the Form 4 row make sorting and exact-form filtering observable."""
    return {
        "cik": 1122304,
        "name": "Synthetic Company",
        "tickers": ["TEST", "TST"],
        "exchanges": ["NYSE", "Nasdaq"],
        "filings": {
            "files": [{"name": "older-submissions.json"}],
            "recent": {
                "accessionNumber": [f"0001193125-25-{n:06}" for n in (1, 2, 3, 4)],
                "filingDate": ["2025-01-01", "2025-03-01", "2025-03-01", "2025-04-01"],
                "reportDate": ["", "2024-12-31", "2024-09-30", "2025-03-31"],
                "form": ["10-Q", "10-K", "10-Q", "4"],
                "primaryDocument": [
                    "one.htm",
                    "two.htm",
                    "three.htm",
                    "xslF345X06/form4.xml",
                ],
            },
        },
    }


def discover(data: Any = None, *, raw: bytes | None = None) -> CompanyFilingsDiscovery:
    """Supply bytes through mock HTTP and check that discovery only requests the main listing."""
    content = (
        raw
        if raw is not None
        else json.dumps(payload() if data is None else data).encode()
    )
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        try:
            return discover_company_filings(
                "1122304", user_agent=USER_AGENT, client=client
            )
        finally:
            assert len(calls) == 1
            assert not client.is_closed


def history_payloads() -> tuple[dict[str, Any], dict[str, Any]]:
    main = payload()
    name = "CIK0001122304-submissions-001.json"
    main["filings"]["files"] = [
        {
            "name": name,
            "filingCount": 5,
            "filingFrom": "2020-12-31",
            "filingTo": "2025-01-01",
        }
    ]
    older = {
        "accessionNumber": [
            main["filings"]["recent"]["accessionNumber"][0],
            *(f"0001193125-{year % 100:02}-000001" for year in range(2021, 2025)),
        ],
        "filingDate": ["2025-01-01", *(f"{year}-02-01" for year in range(2021, 2025))],
        "reportDate": ["", *(f"{year - 1}-12-31" for year in range(2021, 2025))],
        "form": ["10-Q", *("10-K" for _ in range(4))],
        "primaryDocument": [
            "one.htm",
            *(f"annual-{year}.htm" for year in range(2021, 2025)),
        ],
    }
    return main, older


def discover_with_history(
    main: dict[str, Any], older: dict[str, Any]
) -> CompanyFilingsDiscovery:
    name = main["filings"]["files"][0]["name"]
    urls = []

    def respond(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            200,
            json=main if str(request.url) == URL else older,
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = discover_company_filings(
            "1122304",
            user_agent=USER_AGENT,
            client=client,
            _pacer=filing_request.RequestPacer(0),
            include_history=True,
        )
    assert urls == [URL, f"https://data.sec.gov/submissions/{name}"]
    return result


def test_history_crosses_recent_boundary_and_keeps_source_identity() -> None:
    main, older = history_payloads()
    discovery = discover_with_history(main, older)
    assert len(discovery.recent_filings) == 4
    assert len(discovery.history_listings) == 1
    assert len(discovery.all_filings) == 8  # Identical overlap uses the recent row.
    selected = select_company_filings(discovery, forms={"10-K"})
    assert len(selected) == 5
    assert selected[0].source_listing_name is None
    assert all(
        item.source_listing_name == discovery.history_files[0].name
        for item in selected[1:]
    )
    assert selected[-1].report_date == date(2020, 12, 31)


@pytest.mark.parametrize("change", ["count", "length", "date", "cik", "path"])
def test_malformed_history_is_rejected(change: str) -> None:
    main, older = history_payloads()
    if change == "count":
        main["filings"]["files"][0]["filingCount"] = 4
    elif change == "length":
        older["form"].pop()
    elif change == "date":
        older["filingDate"][-1] = "2019-02-99"
    elif change == "cik":
        older["cik"] = "0000000001"
    else:
        main["filings"]["files"][0]["name"] = "../other.json"
    with pytest.raises(CompanyFilingsDiscoveryError):
        discover_with_history(main, older)


def test_conflicting_overlap_is_rejected() -> None:
    main, older = history_payloads()
    older["primaryDocument"][0] = "different.htm"
    with pytest.raises(CompanyFilingsDiscoveryError, match="Conflicting accession"):
        discover_with_history(main, older)


def test_history_manifest_range_mismatch_remains_visible() -> None:
    main, older = history_payloads()
    older["filingDate"][-1] = "2020-12-30"
    discovery = discover_with_history(main, older)
    assert discovery.history_listings[0].range_mismatches == (
        older["accessionNumber"][-1],
    )


def test_request_models_metadata_and_no_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    content = json.dumps(payload(), indent=2).encode() + b"\n"
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert str(request.url) == URL
        assert request.headers["User-Agent"] == USER_AGENT
        assert request.extensions["timeout"]["connect"] == 10
        assert request.extensions["timeout"]["read"] == 30
        return httpx.Response(200, content=content)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Filesystem or network I/O from pure processing")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(builtins, "open", forbidden)
        monkeypatch.setattr(Path, "open", forbidden)
        monkeypatch.setattr(Path, "mkdir", forbidden)
        start = datetime.now(UTC)
        result = discover_company_filings(
            "1122304", user_agent=USER_AGENT, client=client
        )
        assert not client.is_closed and len(calls) == 1
        assert start <= result.retrieved_at <= datetime.now(UTC)
        assert result.retrieved_at.utcoffset() == timedelta(0)
        assert result.cik == "0001122304" and result.submissions_url == URL
        assert result.submissions_content == content
        assert "submissions_content" not in repr(result)
        assert result.entity_name == "Synthetic Company"
        assert result.tickers == ("TEST", "TST") and result.exchanges == (
            "NYSE",
            "Nasdaq",
        )
        assert tuple(f.primary_document for f in result.recent_filings) == (
            "one.htm",
            "two.htm",
            "three.htm",
            "xslF345X06/form4.xml",
        )
        assert result.recent_filings[0].report_date is None
        assert result.recent_filings[1].report_date == date(2024, 12, 31)
        assert (
            result.recent_filings[0].reference.cik
            != result.recent_filings[0].reference.accession_number[:10]
        )
        monkeypatch.setattr(httpx.Client, "get", forbidden)
        assert len(select_company_filings(result, forms={"10-Q"})) == 2
    with pytest.raises(FrozenInstanceError):
        result.cik = "1"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.recent_filings[0].form = "other"  # type: ignore[misc]
    assert not hasattr(result, "__dict__")
    assert not hasattr(result.recent_filings[0], "__dict__")


@pytest.mark.parametrize(
    "path",
    [
        "aapl-20240928.htm",
        "xslF345X06/form4.xml",
        "folder/nested/document.htm",
        "folder%2Fnested%2Fdocument.htm",
    ],
)
def test_safe_relative_primary_document_paths_are_preserved(path: str) -> None:
    data = payload()
    data["filings"]["recent"]["primaryDocument"][0] = path
    assert discover(data).recent_filings[0].primary_document == path


def test_form_4_path_does_not_block_financial_filing_selection() -> None:
    result = discover()
    assert result.recent_filings[3].form == "4"
    assert result.recent_filings[3].primary_document == "xslF345X06/form4.xml"
    selected = select_company_filings(result, forms=("10-K", "10-Q"))
    assert tuple(f.form for f in selected) == ("10-Q", "10-K", "10-Q")
    assert all(f.form != "4" for f in selected)


@pytest.mark.parametrize("bad_response", [False, True])
def test_owned_client_is_closed(
    monkeypatch: pytest.MonkeyPatch, bad_response: bool
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=b"bad" if bad_response else json.dumps(payload()).encode()
            )
        )
    )
    monkeypatch.setattr(company_filings.httpx, "Client", lambda: client)
    if bad_response:
        with pytest.raises(CompanyFilingsDiscoveryError):
            discover_company_filings("1122304", user_agent=USER_AGENT)
    else:
        discover_company_filings("1122304", user_agent=USER_AGENT)
    assert client.is_closed


@pytest.mark.parametrize(
    "cik",
    [None, 1122304, True, "", "0", "0000000000", "12345678901", "１２３", "12x", " 12"],
)
def test_invalid_cik_before_network(cik: Any) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected GET"))
        ) as client,
        pytest.raises(CompanyFilingsDiscoveryError, match="CIK"),
    ):
        discover_company_filings(cik, user_agent=USER_AGENT, client=client)


@pytest.mark.parametrize(
    "agent", [None, "", "contact@example.org", "App", "App\r\ncontact@example.org"]
)
def test_invalid_user_agent_before_network(agent: Any) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected GET"))
        ) as client,
        pytest.raises(CompanyFilingsDiscoveryError, match="User-Agent"),
    ):
        discover_company_filings("1122304", user_agent=agent, client=client)


@pytest.mark.parametrize(
    "raw", [b"not json", b"[]", b'{"cik":1,"cik":2}', b'{"cik":NaN}', b"\xff"]
)
def test_invalid_json(raw: bytes) -> None:
    with pytest.raises(CompanyFilingsDiscoveryError):
        discover(raw=raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("cik", 1),
        ("cik", True),
        ("cik", 1122304.0),
        ("cik", None),
        ("name", ""),
        ("name", " "),
        ("name", None),
        ("tickers", "TEST"),
        ("tickers", [1]),
        ("exchanges", ["NYSE"]),
        ("filings", {}),
        ("filings", {"recent": []}),
    ],
)
def test_invalid_company_metadata(field: str, value: Any) -> None:
    data = payload()
    data[field] = value
    with pytest.raises(CompanyFilingsDiscoveryError):
        discover(data)


@pytest.mark.parametrize(
    "column", ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument"]
)
@pytest.mark.parametrize("mutation", ["missing", "length", "type"])
def test_required_arrays(column: str, mutation: str) -> None:
    data = payload()
    recent = data["filings"]["recent"]
    if mutation == "missing":
        del recent[column]
    elif mutation == "length":
        recent[column].pop()
    else:
        recent[column] = "not an array"
    with pytest.raises(CompanyFilingsDiscoveryError, match="recent"):
        discover(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("filingDate", "20250101"),
        ("filingDate", "2025-02-30"),
        ("filingDate", None),
        ("reportDate", "2025-1-01"),
        ("reportDate", "2025-02-30"),
        ("reportDate", None),
        ("accessionNumber", "bad"),
        ("accessionNumber", 123),
        ("form", ""),
        ("form", "  "),
        ("form", 10),
        ("primaryDocument", ""),
        ("primaryDocument", " "),
        ("primaryDocument", None),
        ("primaryDocument", "../a.htm"),
        ("primaryDocument", "/a.htm"),
        ("primaryDocument", "//example.com/a.htm"),
        ("primaryDocument", "a\\b.htm"),
        ("primaryDocument", ".."),
        ("primaryDocument", "."),
        ("primaryDocument", "folder/./a.htm"),
        ("primaryDocument", "folder/../a.htm"),
        ("primaryDocument", "folder//a.htm"),
        ("primaryDocument", "folder/a.htm/"),
        ("primaryDocument", "%2e%2e%2fa.htm"),
        ("primaryDocument", "%252e%252e"),
        ("primaryDocument", "folder%252f%252e%252e%252fa.htm"),
        ("primaryDocument", "a\n.htm"),
        ("primaryDocument", "a\x85.htm"),
        ("primaryDocument", "C:\\a.htm"),
        ("primaryDocument", "C:/a.htm"),
        ("primaryDocument", "https://example.com/a.htm"),
        ("primaryDocument", "mailto:a@example.com"),
        ("primaryDocument", "a.htm?download=1"),
        ("primaryDocument", "a.htm#section"),
        ("primaryDocument", "a.htm%3Fdownload=1"),
        ("primaryDocument", "a.htm%2523section"),
        ("primaryDocument", "%00a.htm"),
    ],
)
def test_invalid_recent_values(field: str, value: Any) -> None:
    data = payload()
    data["filings"]["recent"][field][0] = value
    with pytest.raises(CompanyFilingsDiscoveryError):
        discover(data)


def test_duplicate_accessions_and_empty_recent() -> None:
    data = payload()
    data["filings"]["recent"]["accessionNumber"][1] = data["filings"]["recent"][
        "accessionNumber"
    ][0]
    with pytest.raises(CompanyFilingsDiscoveryError, match="Duplicate accession"):
        discover(data)
    for column in data["filings"]["recent"]:
        data["filings"]["recent"][column] = []
    data.update(cik="0001122304", tickers=[], exchanges=[])
    assert discover(data).recent_filings == ()


@pytest.mark.parametrize(
    "failure,attempts",
    [
        ("403", 1),
        ("404", 1),
        ("block", 1),
        ("empty", 1),
        ("length", 1),
        ("503", 3),
        ("connect", 3),
        ("timeout", 3),
        ("wait", 1),
    ],
)
def test_shared_request_errors(
    monkeypatch: pytest.MonkeyPatch, failure: str, attempts: int
) -> None:
    time = ControlledTime()
    monkeypatch.setattr(
        filing_request, "RequestPacer", lambda interval, **_: time.pacer(interval)
    )
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if failure == "connect":
            raise httpx.ConnectError("offline", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "block":
            return httpx.Response(
                200,
                content=b"<html><title>SEC.gov | Request Rate Threshold Exceeded</title></html>",
            )
        if failure == "wait":
            return httpx.Response(429, headers={"Retry-After": "61"})
        if failure == "empty":
            return httpx.Response(200, content=b"")
        if failure == "length":
            return httpx.Response(
                200,
                content=json.dumps(payload()).encode(),
                headers={"Content-Length": "1"},
            )
        return httpx.Response(int(failure))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(CompanyFilingsDiscoveryError):
            discover_company_filings("1122304", user_agent=USER_AGENT, client=client)
        assert not client.is_closed
    assert len(calls) == attempts
    assert time.sleeps == ([1, 2] if attempts == 3 else [])


def test_shared_retry_after_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    time = ControlledTime()
    monkeypatch.setattr(
        filing_request, "RequestPacer", lambda interval, **_: time.pacer(interval)
    )
    replies = iter(
        [
            httpx.Response(
                429,
                headers={"Retry-After": "5"},
                content=b"<html><title>SEC.gov | Request Rate Threshold Exceeded</title></html>",
            ),
            httpx.Response(200, content=json.dumps(payload()).encode()),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as client:
        assert (
            discover_company_filings(
                "1122304", user_agent=USER_AGENT, client=client
            ).cik
            == "0001122304"
        )
    assert time.sleeps == [5]


def test_selection_order_filters_boundaries_and_limit() -> None:
    result = discover()
    selected = select_company_filings(result, forms={"10-K", "10-Q"})
    assert tuple(f.primary_document for f in selected) == (
        "three.htm",
        "two.htm",
        "one.htm",
    )
    assert select_company_filings(
        result,
        forms={"10-Q", "10-K"},
        filed_on_or_after=date(2025, 3, 1),
        filed_on_or_before=date(2025, 3, 1),
        limit=1,
    ) == (result.recent_filings[2],)
    assert select_company_filings(result, forms={"10-k"}) == ()
    assert select_company_filings(result, forms={"10-K"}) == (result.recent_filings[1],)
    assert select_company_filings(result, forms={"unknown"}) == ()
    assert isinstance(selected, tuple)


@pytest.mark.parametrize(
    "forms", [None, [], set(), "10-K", [""], [" "], [10], iter(["10-K"])]
)
def test_invalid_forms(forms: Any) -> None:
    with pytest.raises(CompanyFilingsDiscoveryError, match="forms"):
        select_company_filings(discover(), forms=forms)


@pytest.mark.parametrize("limit", [True, 0, -1, 1.0, "1"])
def test_invalid_limit(limit: Any) -> None:
    with pytest.raises(CompanyFilingsDiscoveryError, match="limit"):
        select_company_filings(discover(), forms={"10-K"}, limit=limit)


def test_invalid_selection_input_and_dates() -> None:
    with pytest.raises(CompanyFilingsDiscoveryError, match="discovery"):
        select_company_filings(None, forms={"10-K"})  # type: ignore[arg-type]
    result = discover()
    with pytest.raises(CompanyFilingsDiscoveryError, match="start"):
        select_company_filings(
            result,
            forms={"10-K"},
            filed_on_or_after=date(2025, 3, 2),
            filed_on_or_before=date(2025, 3, 1),
        )
    with pytest.raises(CompanyFilingsDiscoveryError, match="date values"):
        select_company_filings(
            result, forms={"10-K"}, filed_on_or_after=datetime.now(UTC)
        )
