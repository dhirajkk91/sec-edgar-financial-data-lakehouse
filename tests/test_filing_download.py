import gzip
from dataclasses import FrozenInstanceError

import httpx
import pytest

from sec_edgar_lakehouse import (
    DownloadedFile,
    DownloadError,
    FilingReference,
    download_filing_file,
    filing_request,
)

REFERENCE = FilingReference("1122304", "0001193125-15-118890")
USER_AGENT = "DownloadTests contact@example.org"


@pytest.fixture(autouse=True)
def no_real_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filing_request, "sleep", lambda _seconds: None)


def test_downloads_one_file_with_expected_request() -> None:
    requests: list[httpx.Request] = []
    expected_content = b"\x00\x01SEC filing bytes\xff"

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=expected_content)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        downloaded = download_filing_file(
            REFERENCE,
            document_name="report.htm",
            user_agent=USER_AGENT,
            client=client,
        )
        assert not client.is_closed

    assert downloaded == DownloadedFile("report.htm", expected_content)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == (
        "https://www.sec.gov/Archives/edgar/data/1122304/000119312515118890/report.htm"
    )
    assert request.headers["User-Agent"] == USER_AGENT
    assert request.extensions["timeout"]["connect"] == 10
    assert request.extensions["timeout"]["read"] == 30


def test_uses_filing_cik_when_accession_prefix_differs() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"file")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        download_filing_file(
            REFERENCE,
            document_name="issuer_cal.xml",
            user_agent=USER_AGENT,
            client=client,
        )

    assert "/edgar/data/1122304/000119312515118890/" in str(requests[0].url)


# These names must stay one path segment, including the reserved URL characters.
@pytest.mark.parametrize(
    ("document_name", "encoded_name"),
    [
        ("safe name.htm", "safe%20name.htm"),
        ("report?draft.htm", "report%3Fdraft.htm"),
        ("report#draft.htm", "report%23draft.htm"),
    ],
)
def test_document_name_is_encoded_as_one_path_segment(
    document_name: str, encoded_name: str
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"file")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        downloaded = download_filing_file(
            REFERENCE,
            document_name=document_name,
            user_agent=USER_AGENT,
            client=client,
        )

    assert downloaded.document_name == document_name
    assert str(requests[0].url).endswith(f"/{encoded_name}")


def test_downloaded_file_is_immutable() -> None:
    downloaded = DownloadedFile("report.htm", b"file")

    with pytest.raises(FrozenInstanceError):
        downloaded.content = b"changed"  # type: ignore[misc]


def test_default_client_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"file"))
    )
    monkeypatch.setattr(httpx, "Client", lambda: client)

    download_filing_file(
        REFERENCE,
        document_name="report.htm",
        user_agent=USER_AGENT,
    )

    assert client.is_closed


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
        pytest.raises(DownloadError, match="User-Agent"),
    ):
        download_filing_file(
            REFERENCE,
            document_name="report.htm",
            user_agent=user_agent,
            client=client,
        )


def test_empty_document_name_prevents_requests() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Empty document name must be rejected before making a request")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(DownloadError, match="must not be empty"),
    ):
        download_filing_file(
            REFERENCE,
            document_name="",
            user_agent=USER_AGENT,
            client=client,
        )


@pytest.mark.parametrize(
    "document_name",
    [
        ".",
        "..",
        "folder/report.htm",
        "folder\\report.htm",
        "../report.htm",
        "%2e%2e%2freport.htm",
        "folder%2freport.htm",
        "folder%5creport.htm",
        "report%0a.htm",
    ],
)
def test_unsafe_document_name_prevents_requests(document_name: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Unsafe document name must be rejected before making a request")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(DownloadError, match="Unsafe document name"),
    ):
        download_filing_file(
            REFERENCE,
            document_name=document_name,
            user_agent=USER_AGENT,
            client=client,
        )


def test_network_failure_is_a_download_error() -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(DownloadError, match="failed after 3 attempts"),
    ):
        download_filing_file(
            REFERENCE,
            document_name="report.htm",
            user_agent=USER_AGENT,
            client=client,
        )

    assert calls == 3


@pytest.mark.parametrize("status", [302, 403, 404])
def test_non_success_response_is_not_retried_or_redirected(status: int) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            headers={"Location": "https://www.sec.gov/other"},
        )

    with (
        httpx.Client(
            transport=httpx.MockTransport(handle), follow_redirects=True
        ) as client,
        pytest.raises(DownloadError, match=f"HTTP {status}"),
    ):
        download_filing_file(
            REFERENCE,
            document_name="report.htm",
            user_agent=USER_AGENT,
            client=client,
        )

    assert calls == 1


def test_zero_byte_response_is_a_download_error() -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b""))
        ) as client,
        pytest.raises(DownloadError, match="response was empty"),
    ):
        download_filing_file(
            REFERENCE,
            document_name="report.htm",
            user_agent=USER_AGENT,
            client=client,
        )


def test_http_200_sec_rate_limit_page_is_rejected() -> None:
    block = b"<html><head><title>SEC.gov | Request Rate Threshold Exceeded</title></head><body>Wait before requesting more.</body></html>"
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=block))
        ) as client,
        pytest.raises(DownloadError, match="SEC rate-limit page"),
    ):
        download_filing_file(
            REFERENCE, document_name="report.htm", user_agent=USER_AGENT, client=client
        )


def test_normal_filing_can_mention_access_denied() -> None:
    content = b"<html><head><title>10-Q</title></head><body>The claim was access denied.</body></html>"
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    ) as client:
        result = download_filing_file(
            REFERENCE, document_name="report.htm", user_agent=USER_AGENT, client=client
        )
    assert result.content == content


def test_identity_content_length_must_match() -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, content=b"file", headers={"Content-Length": "9"}
                )
            )
        ) as client,
        pytest.raises(DownloadError, match="Content-Length 9"),
    ):
        download_filing_file(
            REFERENCE, document_name="report.htm", user_agent=USER_AGENT, client=client
        )


def test_compressed_wire_length_is_not_compared_to_decoded_body() -> None:
    content = b"<html><body>Filing body</body></html>"
    encoded = gzip.compress(content)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=encoded,
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(encoded)),
                },
            )
        )
    ) as client:
        result = download_filing_file(
            REFERENCE, document_name="report.htm", user_agent=USER_AGENT, client=client
        )
    assert result.content == content
