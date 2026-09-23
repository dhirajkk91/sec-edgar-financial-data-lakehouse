import json
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
<tr><th>Seq</th><th>Document</th></tr>
<tr><td>1</td><td><a href="report.htm">report.htm</a></td></tr>
<tr><td>2</td><td><a href="exhibit.htm">exhibit.htm</a></td></tr>
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
    "package.txt": b"complete submission",
    "issuer.xsd": b"<schema />",
}
REQUEST_ORDER = [INDEX_NAME, *CONTENTS]


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
        return httpx.Response(200, content=CONTENTS[name])

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
    assert [file["status"] for file in manifest["files"]] == ["VERIFIED"] * 6
    assert [file["document_name"] for file in manifest["files"][-2:]] == [
        "filing-index.html",
        "discovery.json",
    ]


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
    # The publisher only saw missing bytes, not the HTTP response.
    assert manifest["files"][3] == {
        "document_name": "issuer.xsd",
        "section": "data-file",
        "required_for_source": False,
        "status": "MISSING",
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
        "MISSING",
        "MISSING",
        "MISSING",
        "VERIFIED",
        "VERIFIED",
    ]


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
    assert names(requests) == [INDEX_NAME]
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
    result = IngestionResult(REFERENCE, "FAILED", None, None, failure)
    assert not hasattr(failure, "__dict__")
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        failure.message = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = "COMPLETE"  # type: ignore[misc]
