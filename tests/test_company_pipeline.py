from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, call

import httpx
import pytest

from sec_edgar_lakehouse import (
    CompanyFiling,
    CompanyFilingProcessResult,
    CompanyFilingsDiscovery,
    CompanyPipelineError,
    CompanyPipelineResult,
    CompanyProcessingResult,
    FilingReference,
    SilverCatalogResult,
    run_company_pipeline,
)
from sec_edgar_lakehouse import company_pipeline as pipeline
from sec_edgar_lakehouse.silver_catalog import SilverCatalogError

CIK = "0000320193"
USER_AGENT = "CompanyPipeline contact@example.org"


def filing(number: int) -> CompanyFiling:
    filed = date(2025, 1, number)
    return CompanyFiling(
        FilingReference(CIK, f"0000320193-25-{number:06}"),
        "10-K",
        filed,
        filed,
        f"filing-{number}.htm",
    )


def processing_result(
    status: str, outcomes: tuple[str, ...]
) -> CompanyProcessingResult:
    filings = tuple(filing(index) for index in range(1, len(outcomes) + 1))
    discovery = CompanyFilingsDiscovery(
        CIK,
        "Apple Inc.",
        ("AAPL",),
        ("Nasdaq",),
        filings,
        f"https://data.sec.gov/submissions/CIK{CIK}.json",
        datetime(2026, 1, 1, tzinfo=UTC),
        b"{}",
    )
    results = tuple(
        CompanyFilingProcessResult(
            item,
            cast(Any, outcome),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        for item, outcome in zip(filings, outcomes, strict=True)
    )
    return CompanyProcessingResult(
        "company-run",
        discovery,
        cast(Any, status),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        results,
    )


def catalog_result(path: Path, status: str = "COMPLETE") -> SilverCatalogResult:
    return SilverCatalogResult(cast(Any, status), path, 1, 0, 3, 2, 1)


def arguments(tmp_path: Path) -> dict[str, Any]:
    return {
        "cik": "320193",
        "user_agent": USER_AGENT,
        "forms": ("10-K", "10-Q"),
        "bronze_directory": tmp_path / "bronze",
        "silver_directory": tmp_path / "silver",
        "database_path": tmp_path / "query" / "sec edgar.duckdb",
        "processing_run_id": "company-run",
        "filed_on_or_after": date(2024, 1, 1),
        "filed_on_or_before": date(2025, 12, 31),
        "limit": 8,
        "client": httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ),
        "request_interval_seconds": 0.75,
    }


def test_complete_processing_refreshes_once_and_forwards_every_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processing = processing_result("COMPLETE", ("COMPLETE",))
    catalog = catalog_result(tmp_path / "query" / "sec edgar.duckdb")
    process = Mock(return_value=processing)
    refresh = Mock(return_value=catalog)
    monkeypatch.setattr(pipeline, "process_company_filings", process)
    monkeypatch.setattr(pipeline, "refresh_silver_catalog", refresh)
    values = arguments(tmp_path)

    try:
        result = run_company_pipeline(**values)
    finally:
        values["client"].close()

    assert result == CompanyPipelineResult(
        "COMPLETE", processing, "COMPLETE", catalog, None
    )
    assert process.call_args == call(
        values["cik"],
        user_agent=values["user_agent"],
        forms=values["forms"],
        bronze_directory=values["bronze_directory"],
        silver_directory=values["silver_directory"],
        processing_run_id=values["processing_run_id"],
        filed_on_or_after=values["filed_on_or_after"],
        filed_on_or_before=values["filed_on_or_before"],
        limit=values["limit"],
        client=values["client"],
        request_interval_seconds=values["request_interval_seconds"],
    )
    refresh.assert_called_once_with(
        silver_directory=values["silver_directory"],
        database_path=values["database_path"],
    )


def test_several_usable_filings_still_refresh_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processing = processing_result(
        "PARTIAL", ("COMPLETE", "PARTIAL", "SKIPPED", "FAILED")
    )
    refresh = Mock(return_value=catalog_result(tmp_path / "catalog.duckdb"))
    monkeypatch.setattr(
        pipeline, "process_company_filings", Mock(return_value=processing)
    )
    monkeypatch.setattr(pipeline, "refresh_silver_catalog", refresh)
    values = arguments(tmp_path)

    try:
        result = run_company_pipeline(**values)
    finally:
        values["client"].close()

    assert result.status == "PARTIAL"
    assert result.processing is processing
    assert refresh.call_count == 1


def test_partial_catalog_makes_complete_processing_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processing = processing_result("COMPLETE", ("COMPLETE",))
    catalog = catalog_result(tmp_path / "catalog.duckdb", "PARTIAL")
    monkeypatch.setattr(
        pipeline, "process_company_filings", Mock(return_value=processing)
    )
    monkeypatch.setattr(pipeline, "refresh_silver_catalog", Mock(return_value=catalog))
    values = arguments(tmp_path)

    try:
        result = run_company_pipeline(**values)
    finally:
        values["client"].close()

    assert result.status == "PARTIAL"
    assert result.catalog_status == "PARTIAL"
    assert result.catalog_result is catalog


def test_catalog_failure_is_structured_and_preserves_processing_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processing = processing_result("COMPLETE", ("COMPLETE",))
    monkeypatch.setattr(
        pipeline, "process_company_filings", Mock(return_value=processing)
    )
    monkeypatch.setattr(
        pipeline,
        "refresh_silver_catalog",
        Mock(side_effect=SilverCatalogError("catalog stayed on its previous snapshot")),
    )
    values = arguments(tmp_path)

    try:
        result = run_company_pipeline(**values)
    finally:
        values["client"].close()

    assert result.status == "PARTIAL"
    assert result.processing is processing
    assert result.catalog_status == "FAILED"
    assert result.catalog_result is None
    assert result.catalog_error == "catalog stayed on its previous snapshot"


@pytest.mark.parametrize(
    ("processing", "expected_status"),
    [
        (processing_result("COMPLETE", ()), "COMPLETE"),
        (processing_result("FAILED", ("FAILED", "NOT_ATTEMPTED")), "FAILED"),
    ],
)
def test_zero_usable_filings_skip_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    processing: CompanyProcessingResult,
    expected_status: str,
) -> None:
    refresh = Mock()
    monkeypatch.setattr(
        pipeline, "process_company_filings", Mock(return_value=processing)
    )
    monkeypatch.setattr(pipeline, "refresh_silver_catalog", refresh)
    values = arguments(tmp_path)
    database = values["database_path"]

    try:
        result = run_company_pipeline(**values)
    finally:
        values["client"].close()

    assert result.status == expected_status
    assert result.catalog_status == "SKIPPED"
    assert result.catalog_result is result.catalog_error is None
    refresh.assert_not_called()
    assert not database.exists()


def test_invalid_database_path_stops_before_processing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = Mock()
    refresh = Mock()
    monkeypatch.setattr(pipeline, "process_company_filings", process)
    monkeypatch.setattr(pipeline, "refresh_silver_catalog", refresh)
    values = arguments(tmp_path)
    values["database_path"] = "catalog.duckdb"

    try:
        with pytest.raises(CompanyPipelineError, match="pathlib.Path"):
            run_company_pipeline(**values)
    finally:
        values["client"].close()

    process.assert_not_called()
    refresh.assert_not_called()
    assert not (tmp_path / "bronze").exists()
    assert not (tmp_path / "silver").exists()


def test_result_is_frozen_and_slotted(tmp_path: Path) -> None:
    processing = processing_result("COMPLETE", ())
    result = CompanyPipelineResult("COMPLETE", processing, "SKIPPED", None, None)

    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.status = "FAILED"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("catalog_status", "catalog", "error"),
    [
        ("COMPLETE", None, None),
        ("PARTIAL", None, None),
        ("COMPLETE", "complete", "unexpected"),
        ("FAILED", "complete", "failed"),
        ("FAILED", None, None),
        ("SKIPPED", "complete", None),
        ("SKIPPED", None, "unexpected"),
    ],
)
def test_result_rejects_invalid_catalog_field_combinations(
    tmp_path: Path,
    catalog_status: str,
    catalog: str | None,
    error: str | None,
) -> None:
    processing = processing_result("COMPLETE", ())
    value = catalog_result(tmp_path / "catalog.duckdb") if catalog else None

    with pytest.raises(ValueError):
        CompanyPipelineResult(
            "COMPLETE", processing, cast(Any, catalog_status), value, error
        )


def test_result_rejects_catalog_status_mismatch(tmp_path: Path) -> None:
    processing = processing_result("COMPLETE", ())

    with pytest.raises(ValueError, match="must match"):
        CompanyPipelineResult(
            "PARTIAL",
            processing,
            "PARTIAL",
            catalog_result(tmp_path / "catalog.duckdb", "COMPLETE"),
            None,
        )


def test_unrelated_catalog_errors_are_not_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processing = processing_result("COMPLETE", ("COMPLETE",))
    monkeypatch.setattr(
        pipeline, "process_company_filings", Mock(return_value=processing)
    )
    monkeypatch.setattr(
        pipeline, "refresh_silver_catalog", Mock(side_effect=RuntimeError("bug"))
    )
    values = arguments(tmp_path)

    try:
        with pytest.raises(RuntimeError, match="bug"):
            run_company_pipeline(**values)
    finally:
        values["client"].close()
