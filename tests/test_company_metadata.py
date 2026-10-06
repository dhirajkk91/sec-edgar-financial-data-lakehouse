from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, call

import pytest

from sec_edgar_lakehouse import (
    CompanyFiling,
    CompanyFilingProcessResult,
    CompanyFilingsDiscovery,
    CompanyFiscalMetadataResult,
    CompanyMetadataLoadError,
    CompanyMetadataLoadResult,
    CompanyPipelineResult,
    CompanyProcessingResult,
    CompanyRunResult,
    FilingFiscalMetadata,
    FilingFiscalMetadataLoadResult,
    FilingMetadataError,
    FilingMetadataIssue,
    FilingMetadataLoadResult,
    FilingReference,
    FiscalMetadataLoadError,
    SilverCatalogResult,
    load_company_run_metadata,
)
from sec_edgar_lakehouse import company_metadata as metadata

CIK = "0000320193"
RUN_ID = "metadata-run"


def company_run(
    root: Path,
    outcomes: tuple[str, ...] = ("COMPLETE", "PARTIAL", "SKIPPED"),
    catalog_status: str = "COMPLETE",
) -> CompanyRunResult:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    filings = tuple(
        CompanyFiling(
            FilingReference(CIK, f"0000320193-25-{index:06}"),
            "10-K",
            date(2025, 1, 1),
            date(2024, 12, 31),
            "filing.htm",
        )
        for index in range(1, len(outcomes) + 1)
    )
    results = tuple(
        CompanyFilingProcessResult(
            filing,
            cast(Any, outcome),
            "COMPLETE",
            root / "bronze" / filing.reference.accession_number,
            root / "bronze" / filing.reference.accession_number / "manifest.json",
            "COMPLETE",
            "PUBLISHED",
            root / "silver" / filing.reference.accession_number,
            None,
            None,
        )
        for filing, outcome in zip(filings, outcomes, strict=True)
    )
    discovery = CompanyFilingsDiscovery(
        CIK, "Apple Inc.", ("AAPL",), ("Nasdaq",), filings, "unused", timestamp, b"{}"
    )
    processing = CompanyProcessingResult(
        RUN_ID, discovery, "PARTIAL", timestamp, timestamp, results
    )
    catalog = (
        SilverCatalogResult(
            cast(Any, catalog_status), root / "catalog.duckdb", 8, 0, 10, 2, 0
        )
        if catalog_status in {"COMPLETE", "PARTIAL"}
        else None
    )
    return CompanyRunResult(
        RUN_ID,
        "PARTIAL",
        CompanyPipelineResult(
            "PARTIAL",
            processing,
            cast(Any, catalog_status),
            catalog,
            "refresh failed" if catalog_status == "FAILED" else None,
        ),
        root / "runs" / f"cik={CIK}" / f"run_id={RUN_ID}" / "run.json",
        None,
        None,
    )


def selected(run: CompanyRunResult) -> tuple[CompanyFilingProcessResult, ...]:
    assert run.pipeline_result is not None
    return run.pipeline_result.processing.filing_results


def filing_result(
    root: Path, issues: tuple[FilingMetadataIssue, ...] = ()
) -> FilingMetadataLoadResult:
    return FilingMetadataLoadResult(
        "PARTIAL" if issues else "COMPLETE",
        root / "catalog.duckdb",
        CIK,
        RUN_ID,
        8,
        7,
        1,
        sum(issue.reason_code == "MISSING_METADATA" for issue in issues),
        sum(issue.reason_code == "METADATA_CONFLICT" for issue in issues),
        issues,
    )


def fiscal_result(
    root: Path,
    reference: FilingReference,
    outcome: str = "INSERTED",
    status: str = "COMPLETE",
) -> FilingFiscalMetadataLoadResult:
    extracted = FilingFiscalMetadata(
        reference,
        "filing.xml",
        "a" * 64,
        "10-K",
        date(2024, 12, 31),
        RUN_ID,
        "b" * 64,
        "1",
        cast(Any, status),
        2024,
        "FY",
        date(2024, 12, 31),
        (),
        (),
    )
    return FilingFiscalMetadataLoadResult(
        cast(Any, outcome), root / "catalog.duckdb", extracted
    )


@pytest.fixture
def loaders(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    filing = Mock()
    fiscal = Mock()
    monkeypatch.setattr(metadata, "load_filing_metadata", filing)
    monkeypatch.setattr(metadata, "load_filing_fiscal_metadata", fiscal)
    return filing, fiscal


def test_order_forwarding_identity_and_unchanged_evidence(
    tmp_path: Path, loaders: tuple[Mock, Mock]
) -> None:
    run = company_run(
        tmp_path, ("SKIPPED", "FAILED", "PARTIAL", "NOT_ATTEMPTED", "COMPLETE")
    )
    usable = (selected(run)[0], selected(run)[2], selected(run)[4])
    loaded_filing = filing_result(tmp_path)
    loaded_fiscal = tuple(
        fiscal_result(
            tmp_path, item.filing.reference, "ALREADY_EXISTS" if i == 0 else "INSERTED"
        )
        for i, item in enumerate(usable)
    )
    filing, fiscal = loaders
    filing.return_value = loaded_filing
    fiscal.side_effect = loaded_fiscal
    sequence = Mock()
    sequence.attach_mock(filing, "filing")
    sequence.attach_mock(fiscal, "fiscal")
    run.run_record_path.parent.mkdir(parents=True)
    evidence = (run.run_record_path, run.run_record_path.with_name("submissions.json"))
    for path in evidence:
        path.write_bytes(b"evidence left to existing loaders")

    result = load_company_run_metadata(
        run, database_path=tmp_path / "alias" / ".." / "catalog.duckdb"
    )

    database = (tmp_path / "catalog.duckdb").resolve()
    assert sequence.mock_calls == [
        call.filing(run_record_path=run.run_record_path, database_path=database),
        *[
            call.fiscal(
                item.bronze_path, item.bronze_manifest_path, database_path=database
            )
            for item in usable
        ],
    ]
    assert result.status == "COMPLETE"
    assert result.company_run is run and run.status == "PARTIAL"
    assert result.database_path == database
    assert result.filing_metadata_result is loaded_filing
    assert result.filing_metadata_result.issues is loaded_filing.issues
    assert result.filing_metadata_error is result.skip_reason is None
    assert tuple(item.reference for item in result.fiscal_results) == tuple(
        item.filing.reference for item in usable
    )
    for item, loaded in zip(result.fiscal_results, loaded_fiscal, strict=True):
        assert item.load_result is loaded
        assert item.outcome == loaded.outcome
        assert item.status == "COMPLETE"
        assert item.error_stage is item.error_message is None
    assert all(
        path.read_bytes() == b"evidence left to existing loaders" for path in evidence
    )


@pytest.mark.parametrize("outcome", ["INSERTED", "ALREADY_EXISTS"])
def test_partial_fiscal_extraction_is_successful(
    tmp_path: Path, loaders: tuple[Mock, Mock], outcome: str
) -> None:
    run = company_run(tmp_path, ("COMPLETE",))
    filing, fiscal = loaders
    filing.return_value = filing_result(tmp_path)
    loaded = fiscal_result(
        tmp_path, selected(run)[0].filing.reference, outcome, "PARTIAL"
    )
    fiscal.return_value = loaded
    result = load_company_run_metadata(run, database_path=tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL"
    assert result.fiscal_results[0].status == "PARTIAL"
    assert result.fiscal_results[0].outcome == outcome
    assert result.fiscal_results[0].load_result is loaded


@pytest.mark.parametrize("reason", ["MISSING_METADATA", "METADATA_CONFLICT"])
def test_metadata_issue_blocks_only_matching_filing(
    tmp_path: Path, loaders: tuple[Mock, Mock], reason: str
) -> None:
    run = company_run(tmp_path, ("COMPLETE", "COMPLETE"))
    first, second = selected(run)
    issue = FilingMetadataIssue(
        CIK, first.filing.reference.accession_number, cast(Any, reason), "unverified"
    )
    filing, fiscal = loaders
    loaded = filing_result(tmp_path, (issue,))
    filing.return_value = loaded
    fiscal.return_value = fiscal_result(tmp_path, second.filing.reference)
    result = load_company_run_metadata(run, database_path=tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL"
    assert result.filing_metadata_result is loaded
    blocked = result.fiscal_results[0]
    assert blocked.status == blocked.outcome == "NOT_ATTEMPTED"
    assert blocked.load_result is None
    assert blocked.error_stage == "FILING_METADATA"
    assert blocked.error_message == f"{reason}: unverified"
    assert result.fiscal_results[1].status == "COMPLETE"
    fiscal.assert_called_once_with(
        second.bronze_path,
        second.bronze_manifest_path,
        database_path=(tmp_path / "catalog.duckdb").resolve(),
    )


@pytest.mark.parametrize("other_company", [False, True])
def test_unrelated_active_issue_does_not_block_selected_filing(
    tmp_path: Path, loaders: tuple[Mock, Mock], other_company: bool
) -> None:
    run = company_run(tmp_path, ("COMPLETE",), "PARTIAL")
    reference = selected(run)[0].filing.reference
    issue = FilingMetadataIssue(
        "0000000001" if other_company else CIK,
        reference.accession_number if other_company else "0000320193-25-999999",
        "METADATA_CONFLICT",
        "another active filing",
    )
    filing, fiscal = loaders
    filing.return_value = filing_result(tmp_path, (issue,))
    fiscal.return_value = fiscal_result(tmp_path, reference)
    result = load_company_run_metadata(run, database_path=tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL"
    assert result.fiscal_results[0].status == "COMPLETE"
    fiscal.assert_called_once()


def test_expected_fiscal_error_continues_to_later_success(
    tmp_path: Path, loaders: tuple[Mock, Mock]
) -> None:
    run = company_run(tmp_path, ("COMPLETE", "COMPLETE"))
    filing, fiscal = loaders
    filing.return_value = filing_result(tmp_path)
    loaded = fiscal_result(tmp_path, selected(run)[1].filing.reference)
    fiscal.side_effect = [FiscalMetadataLoadError("bad fiscal evidence"), loaded]
    result = load_company_run_metadata(run, database_path=tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL"
    failed, successful = result.fiscal_results
    assert failed.status == failed.outcome == "FAILED"
    assert failed.error_stage == "FISCAL_METADATA"
    assert failed.error_message == "bad fiscal evidence"
    assert failed.load_result is None
    assert successful.load_result is loaded
    assert fiscal.call_count == 2


@pytest.mark.parametrize("missing", ["bronze_path", "bronze_manifest_path", "both"])
def test_missing_bronze_paths_fail_and_continue(
    tmp_path: Path, loaders: tuple[Mock, Mock], missing: str
) -> None:
    run = company_run(tmp_path, ("COMPLETE", "COMPLETE"))
    assert run.pipeline_result is not None
    processing = run.pipeline_result.processing
    first, second = selected(run)
    changed_first = replace(
        first,
        bronze_path=None if missing in {"bronze_path", "both"} else first.bronze_path,
        bronze_manifest_path=(
            None
            if missing in {"bronze_manifest_path", "both"}
            else first.bronze_manifest_path
        ),
    )
    run = replace(
        run,
        pipeline_result=replace(
            run.pipeline_result,
            processing=replace(processing, filing_results=(changed_first, second)),
        ),
    )
    filing, fiscal = loaders
    filing.return_value = filing_result(tmp_path)
    fiscal.return_value = fiscal_result(tmp_path, second.filing.reference)
    result = load_company_run_metadata(run, database_path=tmp_path / "catalog.duckdb")
    assert result.status == "PARTIAL"
    failed = result.fiscal_results[0]
    assert failed.status == "FAILED" and failed.error_stage == "FISCAL_METADATA"
    assert failed.error_message is not None and "missing" in failed.error_message
    assert result.fiscal_results[1].status == "COMPLETE"
    fiscal.assert_called_once()


def test_global_filing_failure_blocks_all_usable_fiscal_loads(
    tmp_path: Path, loaders: tuple[Mock, Mock]
) -> None:
    run = company_run(tmp_path, ("COMPLETE", "FAILED", "PARTIAL", "SKIPPED"))
    filing, fiscal = loaders
    filing.side_effect = FilingMetadataError("invalid submissions checksum")
    result = load_company_run_metadata(run, database_path=tmp_path / "catalog.duckdb")
    assert result.status == "FAILED" and result.company_run is run
    assert result.filing_metadata_result is None and result.skip_reason is None
    assert result.filing_metadata_error == "invalid submissions checksum"
    assert len(result.fiscal_results) == 3
    for item in result.fiscal_results:
        assert item.status == item.outcome == "NOT_ATTEMPTED"
        assert item.error_stage == "FILING_METADATA"
        assert item.error_message == result.filing_metadata_error
        assert item.load_result is None
    filing.assert_called_once()
    fiscal.assert_not_called()


@pytest.mark.parametrize("blocked_count", [0, 1, 2])
def test_no_successful_fiscal_results_is_failed(
    tmp_path: Path, loaders: tuple[Mock, Mock], blocked_count: int
) -> None:
    run = company_run(tmp_path, ("COMPLETE", "COMPLETE"))
    issues = tuple(
        FilingMetadataIssue(
            CIK, item.filing.reference.accession_number, "MISSING_METADATA", "absent"
        )
        for item in selected(run)[:blocked_count]
    )
    filing, fiscal = loaders
    filing.return_value = filing_result(tmp_path, issues)
    fiscal.side_effect = FiscalMetadataLoadError("fiscal failure")
    result = load_company_run_metadata(run, database_path=tmp_path / "catalog.duckdb")
    assert result.status == "FAILED"
    assert len(result.fiscal_results) == 2
    assert fiscal.call_count == 2 - blocked_count
    assert all(item.load_result is None for item in result.fiscal_results)


@pytest.mark.parametrize(
    "case", ["no_pipeline", "FAILED", "SKIPPED", "no_usable", "empty"]
)
def test_ineligible_runs_skip_without_loaders(
    tmp_path: Path, loaders: tuple[Mock, Mock], case: str
) -> None:
    run = company_run(
        tmp_path,
        ()
        if case == "empty"
        else ("FAILED", "NOT_ATTEMPTED")
        if case == "no_usable"
        else ("COMPLETE",),
        case if case in {"FAILED", "SKIPPED"} else "COMPLETE",
    )
    if case == "no_pipeline":
        run = replace(run, pipeline_result=None)
    # Eligibility precedes identity checks, which do not apply to skipped runs.
    run = replace(run, run_record_path=tmp_path / "wrong.json")
    result = load_company_run_metadata(run, database_path=tmp_path / "other.duckdb")
    assert result.status == "SKIPPED" and result.skip_reason
    assert result.company_run is run
    assert result.database_path == (tmp_path / "other.duckdb").resolve()
    assert result.filing_metadata_result is result.filing_metadata_error is None
    assert result.fiscal_results == ()
    for loader in loaders:
        loader.assert_not_called()


@pytest.mark.parametrize(
    "mismatch",
    ["database", "run", "name", "run_dir", "cik_dir", "unnormalized_cik", "path_type"],
)
def test_identity_mismatch_rejected_before_loaders(
    tmp_path: Path, loaders: tuple[Mock, Mock], mismatch: str
) -> None:
    run = company_run(tmp_path)
    database = tmp_path / "catalog.duckdb"
    if mismatch == "database":
        database = tmp_path / "other.duckdb"
    elif mismatch == "run":
        run = replace(run, run_id="other-run")
    else:
        paths = {
            "name": run.run_record_path.with_name("other.json"),
            "run_dir": run.run_record_path.parent.parent / "run_id=other" / "run.json",
            "cik_dir": tmp_path / "cik=0000000001" / f"run_id={RUN_ID}" / "run.json",
            "unnormalized_cik": tmp_path
            / "cik=320193"
            / f"run_id={RUN_ID}"
            / "run.json",
            "path_type": "run.json",
        }
        run = replace(run, run_record_path=cast(Any, paths[mismatch]))
    with pytest.raises(CompanyMetadataLoadError):
        load_company_run_metadata(run, database_path=database)
    for loader in loaders:
        loader.assert_not_called()


@pytest.mark.parametrize("argument", ["company_run", "database_path"])
@pytest.mark.parametrize("value", [None, "wrong", 1])
def test_incorrect_argument_types(
    tmp_path: Path, loaders: tuple[Mock, Mock], argument: str, value: object
) -> None:
    values: dict[str, Any] = {
        "company_run": company_run(tmp_path),
        "database_path": tmp_path / "catalog.duckdb",
    }
    values[argument] = value
    with pytest.raises(CompanyMetadataLoadError):
        load_company_run_metadata(**values)
    for loader in loaders:
        loader.assert_not_called()


@pytest.mark.parametrize("stage", ["filing", "fiscal"])
def test_unexpected_loader_exceptions_propagate(
    tmp_path: Path, loaders: tuple[Mock, Mock], stage: str
) -> None:
    run = company_run(tmp_path)
    filing, fiscal = loaders
    filing.return_value = filing_result(tmp_path)
    error = RuntimeError("programming failure")
    (filing if stage == "filing" else fiscal).side_effect = error
    with pytest.raises(RuntimeError) as raised:
        load_company_run_metadata(run, database_path=tmp_path / "catalog.duckdb")
    assert raised.value is error
    assert filing.call_count == 1
    assert fiscal.call_count == (0 if stage == "filing" else 1)


def test_public_result_models_are_frozen_and_slotted(tmp_path: Path) -> None:
    run = company_run(tmp_path)
    result = CompanyMetadataLoadResult("SKIPPED", run, tmp_path, None, None, (), "skip")
    fiscal = CompanyFiscalMetadataResult(
        selected(run)[0].filing.reference, "FAILED", None, "FISCAL_METADATA", "error"
    )
    for item, field in ((result, "status"), (fiscal, "outcome")):
        assert not hasattr(item, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(item, field, "COMPLETE")
