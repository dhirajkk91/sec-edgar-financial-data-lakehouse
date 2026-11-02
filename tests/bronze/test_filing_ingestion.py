"""Bronze end to end for one filing: discover, download the archived files, publish.

A mock SEC serves a small filing. Cases check what is downloaded, what the
manifest records, what happens when a required or optional file fails, SEC
block pages and pacing, and that a published filing is never replaced.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

import sec_edgar_lakehouse.bronze.filing_ingestion as ingestion
from sec_edgar_lakehouse import FilingReference, PublicationError, ingest_filing

REFERENCE = FilingReference("1122304", "0001193125-15-118890")
USER_AGENT = "IngestionTests contact@example.org"
INDEX_NAME = "0001193125-15-118890-index.html"
INSTANCE = b'<xbrl xmlns="http://www.xbrl.org/2003/instance" />'
INDEX = b"""<html><body>
<table summary="Document Format Files">
<tr><th>Seq</th><th>Document</th><th>Type</th></tr>
<tr><td>1</td><td><a href="report.htm">report.htm</a></td><td>10-Q</td></tr>
<tr><td>2</td><td><a href="photo.jpg">photo.jpg</a></td><td>GRAPHIC</td></tr>
<tr><td colspan="2">Complete submission text file</td><td><a href="package.txt">package.txt</a></td></tr>
</table>
<table summary="Data Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
<tr><td>3</td><td>Schema</td><td><a href="issuer.xsd">issuer.xsd</a></td><td>EX-101.SCH</td></tr>
<tr><td>4</td><td>Labels</td><td><a href="issuer_lab.xml">issuer_lab.xml</a></td><td>EX-101.LAB</td></tr>
<tr><td>5</td><td>EXTRACTED XBRL INSTANCE DOCUMENT</td><td><a href="issuer_htm.xml">issuer_htm.xml</a></td><td>XML</td></tr>
</table>
</body></html>"""
CONTENTS = {"issuer.xsd": b"<schema />", "issuer_htm.xml": INSTANCE}
Responder = Callable[[str], httpx.Response | None]


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingestion, "sleep", lambda _seconds: None)


def run(
    tmp_path: Path,
    requests: list[str],
    override: Responder | None = None,
    **kwargs: Any,
) -> ingestion.IngestionResult:
    """Ingest the filing from the mock SEC, recording requested names in order."""

    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append(name)
        if override is not None and (response := override(name)) is not None:
            return response
        if name == INDEX_NAME:
            return httpx.Response(200, content=INDEX)
        return httpx.Response(200, content=CONTENTS[name])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        return ingest_filing(
            REFERENCE,
            user_agent=USER_AGENT,
            bronze_directory=tmp_path / "filings",
            client=client,
            **kwargs,
        )


def manifest(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def staged_manifest(staging: Path | None) -> dict[str, Any]:
    assert staging is not None
    return manifest(next((staging / "manifests").glob("run_id=*.json")))


def test_downloads_only_the_archived_files_and_publishes(tmp_path: Path) -> None:
    requests: list[str] = []
    result = run(tmp_path, requests)

    # No HTML, images, labels or complete submission: only what Silver reads.
    assert requests == [INDEX_NAME, "issuer.xsd", "issuer_htm.xml"]
    assert result.status == "COMPLETE" and result.parser_ready
    assert result.published is not None
    path = result.published.path
    assert path == tmp_path / "filings/cik=0001122304/accession=0001193125-15-118890"
    assert (path / "sec-derived/issuer_htm.xml").read_bytes() == INSTANCE
    assert sorted(p.name for p in path.iterdir()) == [
        "manifests",
        "metadata",
        "sec-derived",
    ]
    saved = manifest(result.published.manifest_path)
    assert saved["archive_policy"] == "xbrl-v1"
    assert saved["source_complete"] is True
    instance = next(f for f in saved["files"] if f["document_name"] == "issuer_htm.xml")
    assert instance["required_for_source"] is True
    assert instance["sha256"] == hashlib.sha256(INSTANCE).hexdigest()
    # The index evidence keeps every listed name, archived or not.
    assert (path / "metadata/filing-index.html").read_bytes() == INDEX
    discovery = json.loads((path / "metadata/discovery.json").read_bytes())
    assert [e["document_name"] for e in discovery["inventory"]] == [
        "issuer.xsd",
        "issuer_htm.xml",
    ]
    assert {e["document_name"] for e in discovery["not_archived"]} == {
        "report.htm",
        "photo.jpg",
        "package.txt",
        "issuer_lab.xml",
    }
    assert not any((tmp_path / ".filing-staging").iterdir())


def test_optional_failure_publishes_partial_and_still_parser_ready(
    tmp_path: Path,
) -> None:
    result = run(
        tmp_path,
        [],
        lambda name: httpx.Response(404) if name == "issuer.xsd" else None,
    )

    assert result.status == "PARTIAL" and result.parser_ready
    assert result.download_failure is not None
    assert result.download_failure.document_name == "issuer.xsd"
    assert result.published is not None
    assert not (result.published.path / "sec-derived/issuer.xsd").exists()


@pytest.mark.parametrize(
    "response",
    [httpx.Response(404), httpx.Response(200, content=b"<xbrl")],
    ids=["missing", "malformed"],
)
def test_required_instance_failure_keeps_staging_and_publishes_nothing(
    tmp_path: Path, response: httpx.Response
) -> None:
    result = run(
        tmp_path, [], lambda name: response if name == "issuer_htm.xml" else None
    )

    assert result.status == "FAILED" and result.published is None
    assert not (tmp_path / "filings").exists()
    saved = staged_manifest(result.staging_path)
    assert saved["status"] == "FAILED" and saved["source_complete"] is False
    # The optional file that did download is kept as evidence.
    assert result.staging_path is not None
    assert (result.staging_path / "sec-derived/issuer.xsd").is_file()


def test_filing_without_xbrl_publishes_evidence_but_is_not_parser_ready(
    tmp_path: Path,
) -> None:
    no_data = INDEX.split(b'<table summary="Data Files">')[0] + b"</body></html>"
    requests: list[str] = []
    result = run(
        tmp_path,
        requests,
        lambda name: (
            httpx.Response(200, content=no_data) if name == INDEX_NAME else None
        ),
    )

    assert requests == [INDEX_NAME]
    assert result.status == "COMPLETE" and not result.parser_ready
    assert result.published is not None
    saved = manifest(result.published.manifest_path)
    assert (
        saved["parser_readiness_reason"]
        == "No XBRL instance document XML file was listed"
    )


@pytest.mark.parametrize("status", [403, 200])
def test_sec_block_page_stops_the_run(tmp_path: Path, status: int) -> None:
    block = b"<html><title>SEC.gov | Request Rate Threshold Exceeded</title></html>"
    requests: list[str] = []
    result = run(
        tmp_path,
        requests,
        lambda name: (
            httpx.Response(status, content=block) if name == "issuer.xsd" else None
        ),
    )

    assert requests == [INDEX_NAME, "issuer.xsd"]
    assert result.status == "FAILED"
    assert result.download_failure is not None and result.download_failure.stop_run


def test_retry_after_is_honoured_and_attempts_are_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, sleeps, calls = [0.0], [], {"n": 0}

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    def throttle_once(name: str) -> httpx.Response | None:
        if name == "issuer_htm.xml" and not calls["n"]:
            calls["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "2"})
        return None

    monkeypatch.setattr(ingestion, "sleep", fake_sleep)
    monkeypatch.setattr(ingestion, "monotonic", lambda: clock[0])
    requests: list[str] = []
    result = run(tmp_path, requests, throttle_once, request_interval_seconds=0.75)

    assert requests == [INDEX_NAME, "issuer.xsd", "issuer_htm.xml", "issuer_htm.xml"]
    assert sleeps == [0.75, 0.75, 2]
    assert result.published is not None
    files = manifest(result.published.manifest_path)["files"]
    assert [f.get("network_attempts") for f in files[:2]] == [1, 2]


def test_short_request_interval_is_rejected_before_any_request(tmp_path: Path) -> None:
    requests: list[str] = []
    with pytest.raises(ValueError, match="at least 0.5 seconds"):
        run(tmp_path, requests, request_interval_seconds=0.1)
    assert requests == []


def test_a_published_filing_is_never_replaced(tmp_path: Path) -> None:
    existing = tmp_path / "filings/cik=0001122304/accession=0001193125-15-118890"
    existing.mkdir(parents=True)
    (existing / "original.txt").write_bytes(b"keep me")

    with pytest.raises(PublicationError, match="already exists") as caught:
        run(tmp_path, [])

    assert [p.name for p in existing.iterdir()] == ["original.txt"]
    assert staged_manifest(caught.value.staging_path)["status"] == "FAILED"


def test_failed_final_move_leaves_no_canonical_filing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_move(source: Path, destination: Path) -> None:
        raise OSError("move failed")

    monkeypatch.setattr(
        "sec_edgar_lakehouse.bronze.filing_publication.os.rename", fail_move
    )
    with pytest.raises(PublicationError, match="move failed") as caught:
        run(tmp_path, [])

    assert not (
        tmp_path / "filings/cik=0001122304/accession=0001193125-15-118890"
    ).exists()
    assert staged_manifest(caught.value.staging_path)["status"] == "FAILED"
