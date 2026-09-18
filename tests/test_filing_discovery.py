from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx
import pytest

from sec_edgar_lakehouse import (
    DiscoveryError,
    FilingDocument,
    FilingReference,
    discover_filing,
)

HTML = (Path(__file__).parent / "fixtures" / "filing-index.html").read_bytes()
REFERENCE = FilingReference("1122304", "0001193125-15-118890")
USER_AGENT = "DiscoveryTests contact@example.org"


def discover(html: bytes = HTML) -> tuple[FilingDocument, ...]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=html)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        try:
            return discover_filing(REFERENCE, user_agent=USER_AGENT, client=client)
        finally:
            assert len(requests) == 1


@pytest.mark.parametrize(
    "reference", [REFERENCE, FilingReference("320193", "0000320193-24-000123")]
)
def test_one_get_with_correct_url_user_agent_and_timeouts(
    reference: FilingReference,
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=HTML)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        discover_filing(reference, user_agent=USER_AGENT, client=client)
        assert not client.is_closed
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{reference.cik_unpadded}/{reference.accession_compact}/"
        f"{reference.accession_number}-index.html"
    )
    assert request.headers["User-Agent"] == USER_AGENT
    assert request.extensions["timeout"]["connect"] == 10
    assert request.extensions["timeout"]["read"] == 30


def test_submitted_documents_preserve_optional_fields_and_unknown_types() -> None:
    assert discover() == (
        FilingDocument("1", "Quarterly report", "report.htm", "10-Q"),
        FilingDocument("2", "Exhibit", "exhibit.htm", "NEW-SEC-TYPE"),
        FilingDocument(None, None, "other.htm", None),
    )


def test_optional_columns_may_be_absent() -> None:
    html = b'<table summary="Document Format Files"><tr><th>Document</th></tr><tr><td>report.htm</td></tr></table>'
    assert discover(html) == (FilingDocument(None, None, "report.htm", None),)


def test_document_is_immutable() -> None:
    document = discover()[0]
    with pytest.raises(FrozenInstanceError):
        document.document_name = "changed.htm"  # type: ignore[misc]


def test_default_client_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=HTML))
    )
    monkeypatch.setattr(httpx, "Client", lambda: client)
    assert len(discover_filing(REFERENCE, user_agent=USER_AGENT)) == 3
    assert client.is_closed


def test_empty_table_without_headers() -> None:
    with pytest.raises(DiscoveryError, match="no document rows"):
        discover(b'<table summary="Document Format Files"></table>')


def test_missing_document_column() -> None:
    with pytest.raises(DiscoveryError, match="missing its Document column"):
        discover(
            b'<table summary="Document Format Files"><tr><th>Seq</th></tr><tr><td>1</td></tr></table>'
        )


@pytest.mark.parametrize(
    "user_agent",
    [
        "",
        "App",
        "contact@example.org",
        "App invalid-email",
        "App contact@example.org\nOther: header",
    ],
)
def test_invalid_user_agent_prevents_requests(user_agent: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid User-Agent must be rejected before making a request")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(DiscoveryError, match="User-Agent"),
    ):
        discover_filing(REFERENCE, user_agent=user_agent, client=client)


@pytest.mark.parametrize("status", [301, 403, 404, 429, 500])
def test_non_success_status_is_not_retried_or_redirected(status: int) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Location": "https://www.sec.gov/other"})

    with (
        httpx.Client(
            transport=httpx.MockTransport(handle), follow_redirects=True
        ) as client,
        pytest.raises(DiscoveryError, match=f"HTTP {status}"),
    ):
        discover_filing(REFERENCE, user_agent=USER_AGENT, client=client)
    assert calls == 1


def test_network_failure_is_a_discovery_error() -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(DiscoveryError, match="Could not fetch filing index"),
    ):
        discover_filing(REFERENCE, user_agent=USER_AGENT, client=client)
    assert calls == 1


def test_missing_table() -> None:
    with pytest.raises(DiscoveryError, match="missing the Document Format Files table"):
        discover(HTML.replace(b"Document Format Files", b"Something Else"))


def test_empty_table() -> None:
    with pytest.raises(DiscoveryError, match="no document rows"):
        discover(
            b'<table summary="Document Format Files"><tr><th>Document</th></tr></table>'
        )


def test_package_link_alone_is_not_a_submitted_document() -> None:
    html = b'<table summary="Document Format Files"><tr><th>Document</th></tr><tr><td>Complete submission text file</td><td><a href="package.txt">package.txt</a></td></tr></table>'
    with pytest.raises(DiscoveryError, match="no document rows"):
        discover(html)


@pytest.mark.parametrize(
    "row",
    [
        b"<td>1</td>",
        b"<td>1</td><td></td><td></td>",
        b'<td>1</td><td></td><td><a href="report.htm"></a></td>',
    ],
)
def test_missing_filename(row: bytes) -> None:
    html = (
        b'<table summary="Document Format Files"><tr><th>Seq</th><th>Description</th><th>Document</th></tr><tr>'
        + row
        + b"</tr></table>"
    )
    with pytest.raises(DiscoveryError, match="missing a document filename"):
        discover(html)


@pytest.mark.parametrize(
    "name",
    [
        b"../report.htm",
        b"folder/report.htm",
        b"folder\\report.htm",
        b".",
        b"..",
        b"%2e%2e%2freport.htm",
    ],
)
def test_unsafe_filename(name: bytes) -> None:
    html = (
        b'<table summary="Document Format Files"><tr><th>Document</th></tr><tr><td>'
        + name
        + b"</td></tr></table>"
    )
    with pytest.raises(DiscoveryError, match="Unsafe document filename"):
        discover(html)
