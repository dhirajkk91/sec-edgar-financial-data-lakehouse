import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from test_silver_parquet import extraction as sample_extraction
from test_silver_parquet import fact

from sec_edgar_lakehouse import (
    CompanyFiling,
    CompanyFilingProcessResult,
    CompanyFilingsDiscovery,
    CompanyProcessingError,
    CompanyProcessingResult,
    DownloadFailure,
    FilingReference,
    IngestionResult,
    PublishedFiling,
    SilverPublicationResult,
    process_company_filings,
)
from sec_edgar_lakehouse import company_processing as processing
from sec_edgar_lakehouse.filing_discovery import DiscoveryError
from sec_edgar_lakehouse.silver_extraction import SilverInputError
from sec_edgar_lakehouse.silver_models import SilverExtraction
from sec_edgar_lakehouse.silver_publication import SilverPublicationError

CIK = "0000320193"
USER_AGENT = "CompanyProcessing contact@example.org"


def company_filing(number: int, filed: date) -> CompanyFiling:
    return CompanyFiling(
        FilingReference(CIK, f"0000320193-25-{number:06}"),
        "10-K" if number % 2 else "10-Q",
        filed,
        filed,
        f"filing-{number}.htm",
    )


def discovery(*filings: CompanyFiling) -> CompanyFilingsDiscovery:
    return CompanyFilingsDiscovery(
        CIK,
        "Apple Inc.",
        ("AAPL",),
        ("Nasdaq",),
        tuple(filings),
        f"https://data.sec.gov/submissions/CIK{CIK}.json",
        datetime(2026, 1, 1, tzinfo=UTC),
        b"{}",
    )


def make_bronze(
    root: Path, filing: CompanyFiling, *, status: str = "COMPLETE"
) -> tuple[Path, Path]:
    path = (
        root
        / f"cik={filing.reference.cik}"
        / f"accession={filing.reference.accession_number}"
    )
    manifest = path / "manifests" / "run_id=bronze.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"status": status, "parser_ready": True}))
    return path, manifest


def extracted(filing: CompanyFiling, *, status: str = "COMPLETE") -> SilverExtraction:
    value = sample_extraction((fact("one", 1, Decimal(42)),))
    return replace(
        value,
        reference=filing.reference,
        status=cast(Any, status),
        accepted_facts=tuple(
            replace(
                item,
                cik=filing.reference.cik,
                accession_number=filing.reference.accession_number,
            )
            for item in value.accepted_facts
        ),
    )


def silver_result(
    root: Path,
    filing: CompanyFiling,
    *,
    outcome: str = "PUBLISHED",
    status: str = "COMPLETE",
    active: bool = True,
) -> SilverPublicationResult:
    version = root / filing.reference.accession_number / "version"
    return SilverPublicationResult(
        cast(Any, outcome),
        cast(Any, status),
        version,
        version / "publication.json",
        version.parent / "active.json",
        version.parent / "run.json",
        active,
    )


def base_arguments(tmp_path: Path) -> dict[str, Any]:
    return {
        "cik": "320193",
        "user_agent": USER_AGENT,
        "forms": ("10-K", "10-Q"),
        "bronze_directory": tmp_path / "bronze",
        "silver_directory": tmp_path / "silver",
        "processing_run_id": "company-run",
    }


def install_discovery(
    monkeypatch: pytest.MonkeyPatch,
    value: CompanyFilingsDiscovery,
    seen: list[tuple[str, httpx.Client, object]] | None = None,
) -> None:
    def fake_discover(
        _cik: str,
        *,
        user_agent: str,
        client: httpx.Client,
        _pacer: object,
    ) -> CompanyFilingsDiscovery:
        assert user_agent == USER_AGENT
        if seen is not None:
            seen.append(("discovery", client, _pacer))
        return value

    monkeypatch.setattr(processing, "discover_company_filings", fake_discover)


def install_successful_silver(
    monkeypatch: pytest.MonkeyPatch,
    silver_root: Path,
    *,
    extraction_status: str = "COMPLETE",
    silver_outcome: str = "PUBLISHED",
    active: bool = True,
    calls: list[str] | None = None,
) -> None:
    def fake_extract(path: Path, _manifest: Path) -> SilverExtraction:
        accession = path.name.removeprefix("accession=")
        filing = company_filing(int(accession[-6:]), date(2025, 1, 1))
        if calls is not None:
            calls.append(accession)
        return extracted(filing, status=extraction_status)

    def fake_publish(
        value: SilverExtraction,
        *,
        bronze_manifest_path: Path,
        silver_directory: Path,
        processing_run_id: str,
    ) -> SilverPublicationResult:
        assert bronze_manifest_path.name.startswith("run_id=")
        assert silver_directory == silver_root
        assert processing_run_id == "company-run"
        filing = CompanyFiling(
            value.reference, "10-K", date(2025, 1, 1), None, "filing.htm"
        )
        return silver_result(
            silver_root,
            filing,
            outcome=silver_outcome,
            status=extraction_status,
            active=active,
        )

    monkeypatch.setattr(processing, "extract_filing_facts", fake_extract)
    monkeypatch.setattr(processing, "publish_silver_extraction", fake_publish)


def test_multiple_filings_use_deterministic_order_one_client_and_one_pacer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filings = (
        company_filing(1, date(2024, 1, 1)),
        company_filing(2, date(2025, 1, 1)),
        company_filing(3, date(2025, 1, 1)),
    )
    seen: list[tuple[str, httpx.Client, object]] = []
    install_discovery(monkeypatch, discovery(*filings), seen)
    arguments = base_arguments(tmp_path)

    def fake_ingest(
        filing: FilingReference,
        *,
        user_agent: str,
        bronze_directory: Path,
        client: httpx.Client,
        request_interval_seconds: float,
        _pacer: object,
    ) -> IngestionResult:
        assert user_agent == USER_AGENT and request_interval_seconds == 0.75
        selected = next(item for item in filings if item.reference == filing)
        path, manifest = make_bronze(bronze_directory, selected)
        seen.append((filing.accession_number, client, _pacer))
        return IngestionResult(
            filing, "COMPLETE", PublishedFiling(path, manifest), None, None, True
        )

    monkeypatch.setattr(processing, "ingest_filing", fake_ingest)
    order: list[str] = []
    install_successful_silver(monkeypatch, arguments["silver_directory"], calls=order)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("GET"))
    ) as client:
        result = process_company_filings(
            **arguments, client=client, request_interval_seconds=0.75
        )
        assert not client.is_closed

    expected = [filings[2], filings[1], filings[0]]
    assert [item.filing for item in result.filing_results] == expected
    assert order == [item.reference.accession_number for item in expected]
    assert result.status == "COMPLETE"
    assert result.selected_count == result.complete_count == result.usable_count == 3
    assert result.partial_count == result.skipped_count == 0
    assert result.failed_count == result.not_attempted_count == 0
    assert all(item[1] is client for item in seen)
    assert len({id(item[2]) for item in seen}) == 1
    assert (
        result.started_at.tzinfo is not None and result.completed_at.tzinfo is not None
    )
    with pytest.raises(FrozenInstanceError):
        result.status = "FAILED"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.filing_results[0].outcome = "FAILED"  # type: ignore[misc]


def test_empty_selection_creates_no_data_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_discovery(monkeypatch, discovery(company_filing(1, date(2025, 1, 1))))
    monkeypatch.setattr(
        processing, "ingest_filing", lambda *a, **k: pytest.fail("ingest")
    )
    arguments = base_arguments(tmp_path)
    arguments["forms"] = ("8-K",)
    result = process_company_filings(**arguments)
    assert result.status == "COMPLETE" and result.filing_results == ()
    assert result.selected_count == result.usable_count == 0
    assert not arguments["bronze_directory"].exists()
    assert not arguments["silver_directory"].exists()


@pytest.mark.parametrize("injected", [False, True])
def test_client_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, injected: bool
) -> None:
    install_discovery(monkeypatch, discovery())
    client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("GET")))
    if injected:
        result = process_company_filings(**base_arguments(tmp_path), client=client)
        assert result.status == "COMPLETE" and not client.is_closed
        client.close()
    else:
        monkeypatch.setattr(processing.httpx, "Client", lambda: client)
        result = process_company_filings(**base_arguments(tmp_path))
        assert result.status == "COMPLETE" and client.is_closed


def test_existing_bronze_is_reused_without_requests_or_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filing = company_filing(1, date(2025, 1, 1))
    arguments = base_arguments(tmp_path)
    path, manifest = make_bronze(arguments["bronze_directory"], filing)
    before = {item: item.read_bytes() for item in path.rglob("*") if item.is_file()}
    install_discovery(monkeypatch, discovery(filing))
    monkeypatch.setattr(processing, "ingest_filing", lambda *a, **k: pytest.fail("SEC"))
    install_successful_silver(
        monkeypatch,
        arguments["silver_directory"],
        silver_outcome="SKIPPED",
    )
    result = process_company_filings(**arguments)
    item = result.filing_results[0]
    assert item.outcome == "SKIPPED" and item.silver_outcome == "SKIPPED"
    assert item.bronze_path == path and item.bronze_manifest_path == manifest
    assert {file: file.read_bytes() for file in before} == before


@pytest.mark.parametrize(
    "problem",
    [
        "zero",
        "multiple",
        "manifest_symlink",
        "manifests_symlink",
        "file",
        "filing_symlink",
    ],
)
def test_invalid_existing_bronze_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    filing = company_filing(1, date(2025, 1, 1))
    arguments = base_arguments(tmp_path)
    path = (
        arguments["bronze_directory"]
        / f"cik={filing.reference.cik}"
        / f"accession={filing.reference.accession_number}"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep"
    marker.write_text("unchanged")
    try:
        if problem == "file":
            path.parent.mkdir(parents=True)
            path.write_text("keep")
        elif problem == "filing_symlink":
            path.parent.mkdir(parents=True)
            path.symlink_to(outside, target_is_directory=True)
        else:
            manifests = path / "manifests"
            path.mkdir(parents=True)
            if problem == "manifests_symlink":
                manifests.symlink_to(outside, target_is_directory=True)
            else:
                manifests.mkdir()
                if problem in {"multiple", "manifest_symlink"}:
                    (manifests / "run_id=one.json").write_text("{}")
                if problem == "multiple":
                    (manifests / "run_id=two.json").write_text("{}")
                if problem == "manifest_symlink":
                    (manifests / "run_id=one.json").unlink()
                    (manifests / "run_id=one.json").symlink_to(marker)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks unavailable: {exc}")
    install_discovery(monkeypatch, discovery(filing))
    monkeypatch.setattr(
        processing, "ingest_filing", lambda *a, **k: pytest.fail("ingest")
    )
    monkeypatch.setattr(
        processing, "extract_filing_facts", lambda *a: pytest.fail("extract")
    )
    result = process_company_filings(**arguments)
    item = result.filing_results[0]
    assert result.status == "FAILED" and item.outcome == "FAILED"
    assert item.error_stage == "BRONZE_RESOLUTION"
    assert marker.read_text() == "unchanged"
    if problem == "file":
        assert path.read_text() == "keep"


@pytest.mark.parametrize(
    "bronze_status,extraction_status,expected",
    [
        ("COMPLETE", "COMPLETE", "COMPLETE"),
        ("PARTIAL", "COMPLETE", "PARTIAL"),
        ("COMPLETE", "PARTIAL", "PARTIAL"),
    ],
)
def test_published_and_partial_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bronze_status: str,
    extraction_status: str,
    expected: str,
) -> None:
    filing = company_filing(1, date(2025, 1, 1))
    arguments = base_arguments(tmp_path)
    make_bronze(arguments["bronze_directory"], filing, status=bronze_status)
    install_discovery(monkeypatch, discovery(filing))
    install_successful_silver(
        monkeypatch,
        arguments["silver_directory"],
        extraction_status=extraction_status,
    )
    result = process_company_filings(**arguments)
    item = result.filing_results[0]
    assert item.outcome == expected
    assert item.bronze_status == bronze_status
    assert item.silver_status == extraction_status
    assert item.silver_outcome == "PUBLISHED"
    assert result.status == ("COMPLETE" if expected == "COMPLETE" else "PARTIAL")


def test_new_bronze_with_active_silver_skip_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filing = company_filing(1, date(2025, 1, 1))
    arguments = base_arguments(tmp_path)
    install_discovery(monkeypatch, discovery(filing))

    def ingest(reference: FilingReference, **kwargs: Any) -> IngestionResult:
        path, manifest = make_bronze(kwargs["bronze_directory"], filing)
        return IngestionResult(
            reference, "COMPLETE", PublishedFiling(path, manifest), None, None, True
        )

    monkeypatch.setattr(processing, "ingest_filing", ingest)
    install_successful_silver(
        monkeypatch, arguments["silver_directory"], silver_outcome="SKIPPED"
    )
    result = process_company_filings(**arguments)
    assert result.filing_results[0].outcome == "COMPLETE"


def test_ordinary_failure_continues_with_later_filing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = company_filing(2, date(2025, 2, 1))
    second = company_filing(1, date(2025, 1, 1))
    arguments = base_arguments(tmp_path)
    install_discovery(monkeypatch, discovery(first, second))
    make_bronze(arguments["bronze_directory"], first)
    make_bronze(arguments["bronze_directory"], second)

    def extract(path: Path, _manifest: Path) -> SilverExtraction:
        if path.name.endswith("000002"):
            raise SilverInputError("bad Bronze")
        return extracted(second)

    monkeypatch.setattr(processing, "extract_filing_facts", extract)
    monkeypatch.setattr(
        processing,
        "publish_silver_extraction",
        lambda value, **kwargs: silver_result(arguments["silver_directory"], second),
    )
    result = process_company_filings(**arguments)
    assert [item.outcome for item in result.filing_results] == ["FAILED", "COMPLETE"]
    assert result.filing_results[0].error_stage == "SILVER_EXTRACTION"
    assert result.status == "PARTIAL" and result.usable_count == 1


@pytest.mark.parametrize("terminal_result", ["exception", "result"])
def test_structured_terminal_stop_preserves_completed_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_result: str
) -> None:
    filings = tuple(
        company_filing(number, date(2025, 4 - number, 1)) for number in (1, 2, 3)
    )
    arguments = base_arguments(tmp_path)
    install_discovery(monkeypatch, discovery(*filings))
    attempted: list[str] = []

    def ingest(reference: FilingReference, **kwargs: Any) -> IngestionResult:
        attempted.append(reference.accession_number)
        selected = next(item for item in filings if item.reference == reference)
        if reference == filings[1].reference:
            if terminal_result == "exception":
                raise DiscoveryError("SEC blocked", stop_run=True)
            return IngestionResult(
                reference,
                "FAILED",
                None,
                tmp_path / "stage",
                DownloadFailure("index", "SEC blocked", 1, True),
                False,
            )
        path, manifest = make_bronze(kwargs["bronze_directory"], selected)
        return IngestionResult(
            reference, "COMPLETE", PublishedFiling(path, manifest), None, None, True
        )

    monkeypatch.setattr(processing, "ingest_filing", ingest)
    install_successful_silver(monkeypatch, arguments["silver_directory"])
    result = process_company_filings(**arguments)
    assert [item.outcome for item in result.filing_results] == [
        "COMPLETE",
        "FAILED",
        "NOT_ATTEMPTED",
    ]
    assert result.filing_results[2].error_stage == "NOT_ATTEMPTED"
    assert attempted == [
        filings[0].reference.accession_number,
        filings[1].reference.accession_number,
    ]
    assert result.status == "PARTIAL"
    assert (
        result.complete_count == result.failed_count == result.not_attempted_count == 1
    )


def test_all_failures_produce_failed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filings = (company_filing(1, date(2025, 1, 1)), company_filing(2, date(2025, 2, 1)))
    arguments = base_arguments(tmp_path)
    install_discovery(monkeypatch, discovery(*filings))
    for item in filings:
        make_bronze(arguments["bronze_directory"], item)
    monkeypatch.setattr(
        processing,
        "extract_filing_facts",
        lambda *args: (_ for _ in ()).throw(SilverInputError("unusable")),
    )
    result = process_company_filings(**arguments)
    assert result.status == "FAILED" and result.failed_count == 2
    assert result.usable_count == 0


@pytest.mark.parametrize("mode", ["error", "inactive"])
def test_silver_publication_failure_is_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    filing = company_filing(1, date(2025, 1, 1))
    arguments = base_arguments(tmp_path)
    make_bronze(arguments["bronze_directory"], filing)
    install_discovery(monkeypatch, discovery(filing))
    monkeypatch.setattr(
        processing, "extract_filing_facts", lambda *args: extracted(filing)
    )
    if mode == "error":
        monkeypatch.setattr(
            processing,
            "publish_silver_extraction",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                SilverPublicationError("cannot publish")
            ),
        )
    else:
        monkeypatch.setattr(
            processing,
            "publish_silver_extraction",
            lambda *args, **kwargs: silver_result(
                arguments["silver_directory"], filing, outcome="SKIPPED", active=False
            ),
        )
    result = process_company_filings(**arguments)
    item = result.filing_results[0]
    assert item.outcome == "FAILED" and item.error_stage == "SILVER_PUBLICATION"
    if mode == "inactive":
        assert item.silver_outcome == "SKIPPED"


@pytest.mark.parametrize(
    "override",
    [
        {"cik": ""},
        {"user_agent": "missing-email"},
        {"forms": ()},
        {"forms": "10-K"},
        {"bronze_directory": "bronze"},
        {"silver_directory": "silver"},
        {"processing_run_id": ""},
        {"processing_run_id": "."},
        {"processing_run_id": "../run"},
        {"processing_run_id": "a\\b"},
        {"processing_run_id": "a\n"},
        {"processing_run_id": "has space"},
        {"processing_run_id": "x" * 129},
        {"filed_on_or_after": date(2025, 2, 1), "filed_on_or_before": date(2025, 1, 1)},
        {"limit": 0},
    ],
)
def test_invalid_input_fails_before_network_or_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: dict[str, Any]
) -> None:
    called = False

    def unexpected(*args: Any, **kwargs: Any) -> CompanyFilingsDiscovery:
        nonlocal called
        called = True
        raise AssertionError("network")

    monkeypatch.setattr(processing, "discover_company_filings", unexpected)
    arguments = base_arguments(tmp_path)
    arguments.update(override)
    with pytest.raises(CompanyProcessingError) as caught:
        process_company_filings(**arguments)
    assert caught.value.__cause__ is not None
    assert not called
    assert not (tmp_path / "bronze").exists()
    assert not (tmp_path / "silver").exists()


@pytest.mark.parametrize(
    "interval",
    [0, 0.1, 0.49, -1, True, float("nan"), float("inf"), float("-inf"), "0.5"],
)
def test_invalid_request_interval_has_no_network_or_filesystem_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interval: Any
) -> None:
    called = False

    def unexpected(*args: Any, **kwargs: Any) -> CompanyFilingsDiscovery:
        nonlocal called
        called = True
        raise AssertionError("network")

    monkeypatch.setattr(processing, "discover_company_filings", unexpected)
    with pytest.raises(
        CompanyProcessingError,
        match="finite number of at least 0.5 seconds",
    ):
        process_company_filings(
            **base_arguments(tmp_path), request_interval_seconds=interval
        )
    assert not called
    assert not (tmp_path / "bronze").exists()
    assert not (tmp_path / "silver").exists()


@pytest.mark.parametrize("interval", [0.5, 0.75])
def test_request_interval_at_or_above_minimum_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interval: float
) -> None:
    seen: list[tuple[str, httpx.Client, object]] = []
    install_discovery(monkeypatch, discovery(), seen)
    result = process_company_filings(
        **base_arguments(tmp_path), request_interval_seconds=interval
    )
    assert result.status == "COMPLETE"
    assert len(seen) == 1
    assert cast(processing.RequestPacer, seen[0][2]).interval == interval


def test_discovery_failure_is_run_level_and_closes_owned_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("GET")))
    monkeypatch.setattr(processing.httpx, "Client", lambda: client)
    error = processing.CompanyFilingsDiscoveryError("submissions unavailable")
    monkeypatch.setattr(
        processing,
        "discover_company_filings",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(CompanyProcessingError) as caught:
        process_company_filings(**base_arguments(tmp_path))
    assert caught.value.__cause__ is error
    assert client.is_closed


def test_core_does_not_refresh_catalog_or_write_company_run_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_discovery(monkeypatch, discovery())
    result = process_company_filings(**base_arguments(tmp_path))
    assert result.status == "COMPLETE"
    assert not (tmp_path / "data" / "runs").exists()
    assert not list(tmp_path.rglob("*.duckdb"))


def test_result_count_properties_reconcile() -> None:
    filing = company_filing(1, date(2025, 1, 1))
    outcomes = cast(
        tuple[Any, ...], ("COMPLETE", "PARTIAL", "SKIPPED", "FAILED", "NOT_ATTEMPTED")
    )
    rows = tuple(
        CompanyFilingProcessResult(
            filing, outcome, None, None, None, None, None, None, None, None
        )
        for outcome in outcomes
    )
    result = CompanyProcessingResult(
        "run",
        discovery(filing),
        "PARTIAL",
        datetime.now(UTC),
        datetime.now(UTC),
        rows,
    )
    assert result.selected_count == 5
    assert result.complete_count == result.partial_count == result.skipped_count == 1
    assert result.failed_count == result.not_attempted_count == 1
    assert result.usable_count == 3
