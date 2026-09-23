import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest

import sec_edgar_lakehouse.filing_ingestion as ingestion
import sec_edgar_lakehouse.filing_publication as publication
from sec_edgar_lakehouse import (
    DownloadFailure,
    FilingReference,
    IngestionResult,
    InventoryError,
    PublicationError,
    PublishedFiling,
    ingest_filing,
)
from sec_edgar_lakehouse.filing_discovery import DiscoveryError
from sec_edgar_lakehouse.filing_download import DownloadedFile
from sec_edgar_lakehouse.filing_storage import StorageError, StoredFile

REFERENCE = FilingReference("1122304", "0001193125-15-118890")
USER_AGENT = "IngestionTests contact@example.org"
INDEX_NAME = "0001193125-15-118890-index.html"
INDEX = b"""<html><body>
<table summary="Document Format Files">
<tr><th>Seq</th><th>Document</th><th>Type</th></tr>
<tr><td>1</td><td><a href="report.htm">report.htm</a></td><td>10-Q</td></tr>
<tr><td>2</td><td><a href="exhibit.htm">exhibit.htm</a></td><td>EX-99</td></tr>
<tr><td colspan="2">Complete submission text file</td><td><a href="package.txt">package.txt</a></td></tr>
</table>
<table summary="Data Files">
<tr><th>Seq</th><th>Document</th></tr>
<tr><td>3</td><td><a href="issuer.xsd">issuer.xsd</a></td></tr>
</table>
</body></html>"""
CONTENTS = {
    "report.htm": b"<html>report</html>",
    "exhibit.htm": b"exhibit bytes",
    "package.txt": b"<SEC-DOCUMENT>0001193125-15-118890\n<SEC-HEADER>FILING\n",
    "issuer.xsd": b"<schema />",
}
REQUEST_ORDER = [INDEX_NAME, *CONTENTS]
INDEX_WITH_EXTRACTED = INDEX.replace(
    b'<tr><th>Seq</th><th>Document</th></tr>\n<tr><td>3</td><td><a href="issuer.xsd">issuer.xsd</a></td></tr>',
    b"<tr><th>Seq</th><th>Description</th><th>Document</th></tr>\n"
    b'<tr><td>3</td><td>Schema</td><td><a href="issuer.xsd">issuer.xsd</a></td></tr>\n'
    b'<tr><td>4</td><td>EXTRACTED XBRL INSTANCE DOCUMENT</td><td><a href="issuer_htm.xml">issuer_htm.xml</a></td></tr>',
)
CONTENTS_WITH_EXTRACTED = CONTENTS | {
    "issuer_htm.xml": b'<xbrl xmlns="http://www.xbrl.org/2003/instance" />'
}


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingestion, "sleep", lambda _seconds: None)


def mock_client(
    requests: list[httpx.Request],
    *,
    failed_name: str | None = None,
    index_content: bytes = INDEX,
    index_status: int = 200,
    request_times: list[float] | None = None,
    clock: list[float] | None = None,
    contents: Mapping[str, bytes] | None = None,
) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request_times is not None and clock is not None:
            request_times.append(clock[0])
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        if name == INDEX_NAME:
            return httpx.Response(index_status, content=index_content)
        if name == failed_name:
            return httpx.Response(404)
        return httpx.Response(
            200, content=(CONTENTS if contents is None else contents)[name]
        )

    return httpx.Client(transport=httpx.MockTransport(respond))


def names(requests: list[httpx.Request]) -> list[str]:
    return [request.url.path.rsplit("/", maxsplit=1)[-1] for request in requests]


def read_manifest(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def canonical_path(bronze_directory: Path) -> Path:
    return bronze_directory / "cik=0001122304" / "accession=0001193125-15-118890"


def test_complete_filing_requests_in_inventory_order_and_publishes(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    bronze_directory = tmp_path / "filings"
    index_response = INDEX + b"\r\n"
    with (
        mock_client(requests, index_content=index_response) as client,
        patch.object(
            ingestion, "build_filing_inventory", wraps=ingestion.build_filing_inventory
        ) as build,
        patch.object(
            ingestion, "publish_filing", wraps=ingestion.publish_filing
        ) as publish,
    ):
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )
        assert not client.is_closed

    assert names(requests) == REQUEST_ORDER
    assert build.call_count == 1
    assert publish.call_count == 1
    assert publish.call_args.kwargs["discovery"].index_content == index_response
    assert result.reference is REFERENCE
    assert result.status == "COMPLETE"
    assert result.staging_path is None
    assert result.download_failure is None
    assert result.parser_ready is False
    assert result.published is not None
    assert result.published.path == canonical_path(bronze_directory)
    assert (
        result.published.path / "metadata" / "filing-index.html"
    ).read_bytes() == index_response
    normalized = json.loads(
        (result.published.path / "metadata" / "discovery.json").read_text(
            encoding="utf-8"
        )
    )
    assert normalized["index_url"] == str(requests[0].url)
    assert [entry["document_name"] for entry in normalized["inventory"]] == list(
        CONTENTS
    )
    assert (
        result.published.path / "submitted" / "report.htm"
    ).read_bytes() == CONTENTS["report.htm"]
    assert (
        result.published.path / "submitted" / "exhibit.htm"
    ).read_bytes() == CONTENTS["exhibit.htm"]
    assert (
        result.published.path / "submission-package" / "package.txt"
    ).read_bytes() == CONTENTS["package.txt"]
    assert (
        result.published.path / "sec-derived" / "issuer.xsd"
    ).read_bytes() == CONTENTS["issuer.xsd"]
    manifest = read_manifest(result.published.manifest_path)
    assert manifest["status"] == "COMPLETE"
    assert manifest["source_complete"] is True
    assert manifest["parser_ready"] is False
    assert "No EXTRACTED" in manifest["parser_readiness_reason"]
    assert [file["status"] for file in manifest["files"]] == ["VERIFIED"] * 6
    assert [file["document_name"] for file in manifest["files"][-2:]] == [
        "filing-index.html",
        "discovery.json",
    ]


def test_extracted_instance_makes_published_filing_parser_ready(tmp_path: Path) -> None:
    assert INDEX_WITH_EXTRACTED != INDEX
    requests: list[httpx.Request] = []
    with mock_client(
        requests,
        index_content=INDEX_WITH_EXTRACTED,
        contents=CONTENTS_WITH_EXTRACTED,
    ) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )

    assert names(requests) == [INDEX_NAME, *CONTENTS_WITH_EXTRACTED]
    assert result.status == "COMPLETE"
    assert result.parser_ready is True
    assert result.published is not None
    assert (result.published.path / "sec-derived" / "issuer_htm.xml").read_bytes() == (
        CONTENTS_WITH_EXTRACTED["issuer_htm.xml"]
    )
    # The second HTML file is an exhibit, not the primary document.
    assert (
        result.published.path / "submitted" / "exhibit.htm"
    ).read_bytes() == b"exhibit bytes"
    manifest = read_manifest(result.published.manifest_path)
    assert manifest["source_complete"] is True
    assert manifest["parser_ready"] is True
    assert "parser_readiness_reason" not in manifest


@pytest.mark.parametrize(
    ("name", "body", "reason"),
    [
        ("report.htm", b"not an HTML document", "Primary filing HTML"),
        ("package.txt", b"not an SEC submission", "SEC submission markers"),
    ],
)
def test_invalid_required_content_blocks_publication(
    tmp_path: Path, name: str, body: bytes, reason: str
) -> None:
    requests: list[httpx.Request] = []
    bronze_directory = tmp_path / "filings"
    with mock_client(requests, contents=CONTENTS | {name: body}) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )

    assert names(requests) == REQUEST_ORDER[: REQUEST_ORDER.index(name) + 1]
    assert result.status == "FAILED"
    assert result.published is None
    assert not canonical_path(bronze_directory).exists()
    stage = result.staging_path
    assert stage is not None
    if name == "package.txt":
        assert (stage / "submitted" / "exhibit.htm").read_bytes() == CONTENTS[
            "exhibit.htm"
        ]
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    failed = next(file for file in manifest["files"] if file["document_name"] == name)
    assert failed["status"] == "FAILED"
    assert reason in failed["error"]
    assert failed["network_attempts"] == 1


def test_malformed_optional_xml_is_removed_from_partial_publication(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    with mock_client(
        requests, contents=CONTENTS | {"issuer.xsd": b"<schema>"}
    ) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )

    assert names(requests) == REQUEST_ORDER
    assert result.status == "PARTIAL"
    assert result.download_failure is not None
    assert "Malformed XML" in result.download_failure.message
    assert result.parser_ready is False
    assert result.published is not None
    assert not (result.published.path / "sec-derived" / "issuer.xsd").exists()
    manifest = read_manifest(result.published.manifest_path)
    assert manifest["source_complete"] is True
    assert manifest["files"][3]["status"] == "FAILED"
    assert "Malformed XML" in manifest["files"][3]["error"]


def test_malformed_extracted_instance_is_partial_and_not_parser_ready(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    with mock_client(
        requests,
        index_content=INDEX_WITH_EXTRACTED,
        contents=CONTENTS_WITH_EXTRACTED | {"issuer_htm.xml": b"<xbrl>"},
    ) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )

    assert result.status == "PARTIAL"
    assert result.parser_ready is False
    assert result.published is not None
    assert not (result.published.path / "sec-derived" / "issuer_htm.xml").exists()
    manifest = read_manifest(result.published.manifest_path)
    assert manifest["source_complete"] is True
    assert manifest["parser_ready"] is False
    assert "issuer_htm.xml is FAILED" in manifest["parser_readiness_reason"]
    assert "Malformed XML" in manifest["parser_readiness_reason"]


def test_http_200_sec_block_stops_document_requests_and_records_reason(
    tmp_path: Path,
) -> None:
    block = b"<html><head><title>SEC.gov | Request Rate Threshold Exceeded</title></head></html>"
    requests: list[httpx.Request] = []
    bronze_directory = tmp_path / "filings"
    with mock_client(requests, contents=CONTENTS | {"exhibit.htm": block}) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )

    assert names(requests) == REQUEST_ORDER[:3]
    assert result.status == "FAILED"
    assert result.download_failure is not None
    assert "SEC rate-limit page" in result.download_failure.message
    assert not canonical_path(bronze_directory).exists()
    assert result.staging_path is not None
    assert (result.staging_path / "submitted" / "report.htm").read_bytes() == CONTENTS[
        "report.htm"
    ]
    manifest = read_manifest(
        next((result.staging_path / "manifests").glob("run_id=*.json"))
    )
    assert manifest["source_complete"] is False
    assert manifest["files"][1]["status"] == "FAILED"
    assert "SEC rate-limit page" in manifest["files"][1]["error"]
    assert manifest["files"][2]["status"] == "MISSING"


def test_normal_filing_text_can_mention_access_denied(tmp_path: Path) -> None:
    report = b"<html><head><title>10-Q</title></head><body>The request was access denied.</body></html>"
    requests: list[httpx.Request] = []
    with mock_client(requests, contents=CONTENTS | {"report.htm": report}) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert result.status == "COMPLETE"
    assert result.published is not None
    assert (result.published.path / "submitted" / "report.htm").read_bytes() == report


def test_ambiguous_extracted_instances_leave_parser_unready(tmp_path: Path) -> None:
    index = INDEX_WITH_EXTRACTED.replace(
        b"</table>\n</body></html>",
        b'<tr><td>5</td><td>EXTRACTED XBRL INSTANCE DOCUMENT</td><td><a href="second_htm.xml">second_htm.xml</a></td></tr>\n</table>\n</body></html>',
    )
    contents = CONTENTS_WITH_EXTRACTED | {"second_htm.xml": b"<xbrl />"}
    requests: list[httpx.Request] = []
    with mock_client(requests, index_content=index, contents=contents) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert result.status == "COMPLETE"
    assert result.parser_ready is False
    assert result.published is not None
    manifest = read_manifest(result.published.manifest_path)
    assert manifest["source_complete"] is True
    assert manifest["parser_ready"] is False
    assert "Multiple EXTRACTED" in manifest["parser_readiness_reason"]


def test_required_content_failure_stops_before_parser_input(tmp_path: Path) -> None:
    contents = CONTENTS_WITH_EXTRACTED | {"package.txt": b"invalid package"}
    requests: list[httpx.Request] = []
    bronze_directory = tmp_path / "filings"
    with mock_client(
        requests, index_content=INDEX_WITH_EXTRACTED, contents=contents
    ) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )
    assert not canonical_path(bronze_directory).exists()
    assert names(requests) == REQUEST_ORDER[:4]
    stage = result.staging_path
    assert stage is not None
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert manifest["parser_ready"] is False
    assert manifest["files"][4]["status"] == "MISSING"
    assert not (stage / "sec-derived" / "issuer_htm.xml").exists()


def test_optional_final_download_failure_publishes_partial(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    with mock_client(requests, failed_name="issuer.xsd") as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )

    assert names(requests) == REQUEST_ORDER
    assert result.status == "PARTIAL"
    assert result.published is not None
    assert result.staging_path is None
    assert result.download_failure is not None
    assert result.download_failure.document_name == "issuer.xsd"
    assert result.download_failure.message == (
        f"Filing file request returned HTTP 404: {requests[-1].url}"
    )
    manifest = read_manifest(result.published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["source_complete"] is True
    assert [file["status"] for file in manifest["files"][-2:]] == [
        "VERIFIED",
        "VERIFIED",
    ]
    # Only the requested file gets a network attempt count.
    assert manifest["files"][3] == {
        "document_name": "issuer.xsd",
        "section": "data-file",
        "required_for_source": False,
        "status": "FAILED",
        "error": result.download_failure.message,
        "network_attempts": 1,
    }


def test_optional_storage_failure_sets_partial_without_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_store = publication.store_downloaded_file

    def fail_optional_storage(
        downloaded: DownloadedFile, *, destination_directory: Path
    ) -> StoredFile:
        if downloaded.document_name == "issuer.xsd":
            raise StorageError("cannot store optional file")
        return original_store(downloaded, destination_directory=destination_directory)

    monkeypatch.setattr(publication, "store_downloaded_file", fail_optional_storage)
    requests: list[httpx.Request] = []
    with mock_client(requests) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )

    assert names(requests) == REQUEST_ORDER
    assert result.status == "PARTIAL"
    assert result.download_failure is None
    assert result.published is not None
    manifest = read_manifest(result.published.manifest_path)
    assert manifest["status"] == "PARTIAL"
    assert manifest["source_complete"] is True
    assert manifest["files"][3]["status"] == "FAILED"
    assert not (result.published.path / "sec-derived" / "issuer.xsd").exists()


def test_metadata_failure_surfaces_staging_path_through_ingestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_store = publication.store_downloaded_file

    def fail_index_storage(
        downloaded: DownloadedFile, *, destination_directory: Path
    ) -> StoredFile:
        if downloaded.document_name == "filing-index.html":
            raise StorageError("cannot archive SEC index")
        return original_store(downloaded, destination_directory=destination_directory)

    monkeypatch.setattr(publication, "store_downloaded_file", fail_index_storage)
    requests: list[httpx.Request] = []
    bronze_directory = tmp_path / "filings"
    with (
        mock_client(requests) as client,
        pytest.raises(PublicationError, match="filing-index.html") as caught,
    ):
        ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )

    assert names(requests) == REQUEST_ORDER
    assert not canonical_path(bronze_directory).exists()
    stage = caught.value.staging_path
    assert stage is not None
    assert (stage / "submitted" / "report.htm").read_bytes() == CONTENTS["report.htm"]
    manifest = read_manifest(next((stage / "manifests").glob("run_id=*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert "filing-index.html" in manifest["error"]


def test_required_download_failure_keeps_verified_staging(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    bronze_directory = tmp_path / "filings"
    with mock_client(requests, failed_name="exhibit.htm") as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )
        assert not client.is_closed

    assert names(requests) == REQUEST_ORDER[:3]
    assert result.status == "FAILED"
    assert result.published is None
    assert result.download_failure is not None
    assert result.download_failure.document_name == "exhibit.htm"
    assert result.download_failure.message == (
        f"Filing file request returned HTTP 404: {requests[-1].url}"
    )
    assert not canonical_path(bronze_directory).exists()
    assert result.staging_path is not None
    assert (result.staging_path / "submitted" / "report.htm").read_bytes() == CONTENTS[
        "report.htm"
    ]
    manifest = read_manifest(
        next((result.staging_path / "manifests").glob("run_id=*.json"))
    )
    assert manifest["status"] == "FAILED"
    assert manifest["source_complete"] is False
    assert [file["status"] for file in manifest["files"]] == [
        "VERIFIED",
        "FAILED",
        "MISSING",
        "MISSING",
        "VERIFIED",
        "VERIFIED",
    ]
    assert manifest["files"][1]["error"] == result.download_failure.message


def test_required_download_failure_does_not_hide_unstaged_publication_error(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    failure = PublicationError("could not create staging")
    with (
        mock_client(requests, failed_name="exhibit.htm") as client,
        patch.object(ingestion, "publish_filing", side_effect=failure) as publish,
        pytest.raises(PublicationError) as caught,
    ):
        ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )

    assert caught.value is failure
    assert names(requests) == REQUEST_ORDER[:3]
    assert publish.call_count == 1
    assert publish.call_args.args[1] == {"report.htm": CONTENTS["report.htm"]}


def test_rejects_unexpected_published_manifest_status(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"status": "FAILED"}', encoding="utf-8")
    published = PublishedFiling(tmp_path, manifest_path)
    requests: list[httpx.Request] = []
    with (
        mock_client(requests) as client,
        patch.object(ingestion, "publish_filing", return_value=published),
        pytest.raises(PublicationError, match="invalid status"),
    ):
        ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )


def test_discovery_failure_stops_before_download_or_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        ingestion, "publish_filing", lambda *_args, **_kwargs: pytest.fail("published")
    )
    with mock_client(requests, index_status=500) as client:
        with pytest.raises(DiscoveryError):
            ingest_filing(
                REFERENCE,
                user_agent=USER_AGENT,
                bronze_directory=tmp_path / "filings",
                client=client,
            )
        assert not client.is_closed
    assert names(requests) == [INDEX_NAME] * 3
    assert not (tmp_path / "filings").exists()


def test_inventory_failure_stops_before_download_or_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        ingestion, "publish_filing", lambda *_args, **_kwargs: pytest.fail("published")
    )
    duplicate_name_index = INDEX.replace(b"issuer.xsd", b"report.htm")
    with (
        mock_client(requests, index_content=duplicate_name_index) as client,
        pytest.raises(InventoryError),
    ):
        ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert names(requests) == [INDEX_NAME]
    assert not (tmp_path / "filings").exists()


def test_paces_discovery_and_download_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    clock = [0.0]
    request_times: list[float] = []
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(ingestion, "sleep", fake_sleep)
    monkeypatch.setattr(ingestion, "monotonic", lambda: clock[0])
    with mock_client(requests, request_times=request_times, clock=clock) as client:
        ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
            request_interval_seconds=0.75,
        )

    assert names(requests) == REQUEST_ORDER
    assert sleeps == [0.75] * (len(REQUEST_ORDER) - 1)
    assert request_times == [0.0, 0.75, 1.5, 2.25, 3.0]


@pytest.mark.parametrize("index_status", [200, 500])
def test_owned_client_is_closed_even_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index_status: int
) -> None:
    requests: list[httpx.Request] = []
    owned_client = mock_client(requests, index_status=index_status)
    monkeypatch.setattr(ingestion.httpx, "Client", lambda: owned_client)
    if index_status == 500:
        with pytest.raises(DiscoveryError):
            ingest_filing(
                REFERENCE, user_agent=USER_AGENT, bronze_directory=tmp_path / "filings"
            )
    else:
        assert (
            ingest_filing(
                REFERENCE, user_agent=USER_AGENT, bronze_directory=tmp_path / "filings"
            ).status
            == "COMPLETE"
        )
    assert owned_client.is_closed


def test_short_request_interval_is_rejected_before_http(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    with (
        mock_client(requests) as client,
        pytest.raises(ValueError, match="at least 0.5"),
    ):
        ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
            request_interval_seconds=0.49,
        )
    assert requests == []


@pytest.mark.parametrize("failed_name", [None, "issuer.xsd"])
def test_publication_error_is_not_hidden_by_successful_required_downloads(
    tmp_path: Path, failed_name: str | None
) -> None:
    bronze_directory = tmp_path / "filings"
    existing = canonical_path(bronze_directory)
    existing.mkdir(parents=True)
    (existing / "marker.txt").write_bytes(b"keep me")
    requests: list[httpx.Request] = []
    with (
        mock_client(requests, failed_name=failed_name) as client,
        pytest.raises(PublicationError),
    ):
        ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )
    assert names(requests) == REQUEST_ORDER
    assert (existing / "marker.txt").read_bytes() == b"keep me"


def test_result_types_are_frozen_and_slotted() -> None:
    failure = DownloadFailure("report.htm", "unavailable")
    result = IngestionResult(REFERENCE, "FAILED", None, None, failure, False)
    assert not hasattr(failure, "__dict__")
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        failure.message = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = "COMPLETE"  # type: ignore[misc]


def test_index_retry_recovery_uses_same_pace_as_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    sleeps: list[float] = []
    request_times: list[float] = []
    requests: list[str] = []
    index_calls = 0

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal index_calls
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append(name)
        request_times.append(clock[0])
        if name == INDEX_NAME:
            index_calls += 1
            return httpx.Response(500 if index_calls == 1 else 200, content=INDEX)
        return httpx.Response(200, content=CONTENTS[name])

    monkeypatch.setattr(ingestion, "sleep", fake_sleep)
    monkeypatch.setattr(ingestion, "monotonic", lambda: clock[0])
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
            request_interval_seconds=0.75,
        )
    assert result.status == "COMPLETE"
    assert requests == [INDEX_NAME, INDEX_NAME, *CONTENTS]
    assert request_times == [0, 1, 1.75, 2.5, 3.25, 4.0]
    assert sleeps == [1, 0.75, 0.75, 0.75, 0.75]


def test_document_retry_success_records_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    sleeps: list[float] = []
    requests: list[str] = []
    report_calls = 0

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal report_calls
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append(name)
        if name == INDEX_NAME:
            return httpx.Response(200, content=INDEX)
        if name == "report.htm":
            report_calls += 1
            if report_calls == 1:
                return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, content=CONTENTS[name])

    monkeypatch.setattr(ingestion, "sleep", fake_sleep)
    monkeypatch.setattr(ingestion, "monotonic", lambda: clock[0])
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert requests == [INDEX_NAME, "report.htm", "report.htm", *list(CONTENTS)[1:]]
    assert sleeps == [0.5, 2, 0.5, 0.5, 0.5]
    assert result.published is not None
    manifest = read_manifest(result.published.manifest_path)
    assert manifest["files"][0]["network_attempts"] == 2
    assert all(file["network_attempts"] == 1 for file in manifest["files"][1:4])
    assert all("network_attempts" not in file for file in manifest["files"][4:])


def test_optional_exhaustion_continues_to_later_file_and_publishes_partial(
    tmp_path: Path,
) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append(name)
        if name == INDEX_NAME:
            return httpx.Response(200, content=INDEX_WITH_EXTRACTED)
        if name == "issuer.xsd":
            return httpx.Response(503)
        return httpx.Response(200, content=CONTENTS_WITH_EXTRACTED[name])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert requests == [INDEX_NAME, *list(CONTENTS)[:3], "issuer.xsd"] + [
        "issuer.xsd",
        "issuer.xsd",
        "issuer_htm.xml",
    ]
    assert result.status == "PARTIAL"
    assert result.parser_ready is True
    assert result.published is not None
    manifest = read_manifest(result.published.manifest_path)
    by_name = {file["document_name"]: file for file in manifest["files"]}
    assert by_name["issuer.xsd"]["status"] == "FAILED"
    assert by_name["issuer.xsd"]["network_attempts"] == 3
    assert "HTTP 503" in by_name["issuer.xsd"]["error"]
    assert by_name["issuer_htm.xml"]["status"] == "VERIFIED"
    assert by_name["issuer_htm.xml"]["network_attempts"] == 1


def test_multiple_optional_failures_keep_each_manifest_reason(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        if name == INDEX_NAME:
            return httpx.Response(200, content=INDEX_WITH_EXTRACTED)
        if name == "issuer.xsd":
            return httpx.Response(404)
        if name == "issuer_htm.xml":
            return httpx.Response(500)
        return httpx.Response(200, content=CONTENTS_WITH_EXTRACTED[name])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert result.status == "PARTIAL"
    assert result.published is not None
    manifest = read_manifest(result.published.manifest_path)
    by_name = {file["document_name"]: file for file in manifest["files"]}
    assert by_name["issuer.xsd"]["status"] == "FAILED"
    assert by_name["issuer.xsd"]["network_attempts"] == 1
    assert "HTTP 404" in by_name["issuer.xsd"]["error"]
    assert by_name["issuer_htm.xml"]["status"] == "FAILED"
    assert by_name["issuer_htm.xml"]["network_attempts"] == 3
    assert "HTTP 500" in by_name["issuer_htm.xml"]["error"]


@pytest.mark.parametrize("status", [403, 200])
def test_access_block_on_optional_file_fails_entire_run(
    tmp_path: Path, status: int
) -> None:
    requests: list[str] = []
    block = b"<html><head><title>SEC.gov | Request Rate Threshold Exceeded</title></head></html>"

    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append(name)
        if name == INDEX_NAME:
            return httpx.Response(200, content=INDEX_WITH_EXTRACTED)
        if name == "issuer.xsd":
            return httpx.Response(status, content=block)
        return httpx.Response(200, content=CONTENTS_WITH_EXTRACTED[name])

    bronze_directory = tmp_path / "filings"
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )
    assert requests == [INDEX_NAME, *list(CONTENTS)[:3], "issuer.xsd"]
    assert result.status == "FAILED"
    assert result.published is None
    assert not canonical_path(bronze_directory).exists()
    assert result.staging_path is not None
    manifest = read_manifest(next((result.staging_path / "manifests").glob("*.json")))
    assert manifest["status"] == "FAILED"
    assert manifest["files"][3]["status"] == "FAILED"
    assert manifest["files"][3]["network_attempts"] == 1
    assert manifest["files"][4]["status"] == "MISSING"


def test_retry_after_over_limit_on_optional_file_stops_run(tmp_path: Path) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append(name)
        if name == INDEX_NAME:
            return httpx.Response(200, content=INDEX_WITH_EXTRACTED)
        if name == "issuer.xsd":
            return httpx.Response(429, headers={"Retry-After": "61"})
        return httpx.Response(200, content=CONTENTS_WITH_EXTRACTED[name])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert requests == [INDEX_NAME, *list(CONTENTS)[:3], "issuer.xsd"]
    assert result.status == "FAILED"
    assert result.staging_path is not None
    manifest = read_manifest(next((result.staging_path / "manifests").glob("*.json")))
    assert "60-second limit" in manifest["error"]
    assert manifest["files"][3]["network_attempts"] == 1
    assert manifest["files"][4]["status"] == "MISSING"


def test_required_exhaustion_stops_later_downloads_and_stages_failed(
    tmp_path: Path,
) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append(name)
        if name == INDEX_NAME:
            return httpx.Response(200, content=INDEX)
        if name == "exhibit.htm":
            return httpx.Response(500)
        return httpx.Response(200, content=CONTENTS[name])

    bronze_directory = tmp_path / "filings"
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=bronze_directory,
            client=client,
        )
    assert requests == [
        INDEX_NAME,
        "report.htm",
        "exhibit.htm",
        "exhibit.htm",
        "exhibit.htm",
    ]
    assert result.status == "FAILED"
    assert not canonical_path(bronze_directory).exists()
    assert result.staging_path is not None
    manifest = read_manifest(next((result.staging_path / "manifests").glob("*.json")))
    assert [item["status"] for item in manifest["files"][:4]] == [
        "VERIFIED",
        "FAILED",
        "MISSING",
        "MISSING",
    ]
    assert manifest["files"][1]["network_attempts"] == 3
    assert "HTTP 500" in manifest["files"][1]["error"]


def test_optional_invalid_content_continues_to_later_file(tmp_path: Path) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append(name)
        if name == INDEX_NAME:
            return httpx.Response(200, content=INDEX_WITH_EXTRACTED)
        body = b"<schema>" if name == "issuer.xsd" else CONTENTS_WITH_EXTRACTED[name]
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert requests == [INDEX_NAME, *CONTENTS_WITH_EXTRACTED]
    assert result.status == "PARTIAL"
    assert result.parser_ready is True
    assert result.published is not None
    manifest = read_manifest(result.published.manifest_path)
    by_name = {item["document_name"]: item for item in manifest["files"]}
    assert by_name["issuer.xsd"]["status"] == "FAILED"
    assert by_name["issuer.xsd"]["network_attempts"] == 1
    assert "Malformed XML" in by_name["issuer.xsd"]["error"]
    assert by_name["issuer_htm.xml"]["status"] == "VERIFIED"


def test_index_exhaustion_exposes_attempt_count_and_final_reason(
    tmp_path: Path,
) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path.rsplit("/", maxsplit=1)[-1])
        return httpx.Response(503, content=b"busy")

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(
            DiscoveryError, match="failed after 3 attempts: HTTP 503"
        ) as caught,
    ):
        ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
        )
    assert caught.value.attempts == 3
    assert requests == [INDEX_NAME] * 3
    assert not (tmp_path / "filings").exists()
