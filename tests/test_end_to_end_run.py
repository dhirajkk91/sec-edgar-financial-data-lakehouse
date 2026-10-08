import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
import test_end_to_end_pipeline as core_fixtures
from test_end_to_end_pipeline import company_result, dbt_result, metadata_result

from sec_edgar_lakehouse import (
    CompanyFiscalMetadataResult,
    EndToEndPipelineError,
    EndToEndPipelineResult,
    EndToEndRunError,
    FilingMetadataIssue,
    FiscalMetadataIssue,
    execute_end_to_end_pipeline_run,
)
from sec_edgar_lakehouse import end_to_end_run as run

core_inputs = core_fixtures.inputs


@pytest.fixture
def inputs(tmp_path: Path, core_inputs: dict[str, Any]) -> dict[str, Any]:
    return {**core_inputs, "end_to_end_run_directory": tmp_path / "final summaries"}


def pipeline_result(root: Path, status: str = "COMPLETE") -> EndToEndPipelineResult:
    company = company_result(root, status=status)
    company.run_record_path.parent.mkdir(parents=True, exist_ok=True)
    company.run_record_path.write_bytes(b'{"original_company_record": true}\n')
    company.run_record_path.with_name("submissions.json").write_bytes(
        b"exact source bytes"
    )
    return EndToEndPipelineResult(
        cast(Any, status),
        root / "catalog.duckdb",
        company,
        None,
        "COMPLETE",
        metadata_result(company, root),
        None,
        None,
        "COMPLETE",
        dbt_result(root),
        None,
        None,
        root / "fresh dbt artifacts",
    )


@pytest.fixture
def coordinator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Mock:
    execute = Mock(return_value=pipeline_result(tmp_path))
    monkeypatch.setattr(run, "run_end_to_end_pipeline", execute)
    return execute


def record_path(inputs: dict[str, Any]) -> Path:
    return (
        inputs["end_to_end_run_directory"]
        / "cik=0000320193"
        / f"run_id={inputs['processing_run_id']}.json"
    )


@pytest.mark.parametrize("status", ["COMPLETE", "PARTIAL", "FAILED"])
def test_exact_forwarding_identity_status_and_json_contract(
    tmp_path: Path, inputs: dict[str, Any], coordinator: Mock, status: str
) -> None:
    pipeline = pipeline_result(tmp_path, status)
    coordinator.return_value = pipeline
    inputs["forms"] = ("10-Q", "10-K", "10-Q", "10-k")
    assert not inputs["database_path"].resolve().exists()
    source = pipeline.company_run
    assert source is not None
    original = source.run_record_path.read_bytes()
    result = execute_end_to_end_pipeline_run(**inputs)
    coordinator.assert_called_once_with(
        inputs["cik"],
        **{
            key: value
            for key, value in inputs.items()
            if key not in {"cik", "end_to_end_run_directory"}
        },
    )
    assert result.pipeline_result is pipeline and result.status == status
    assert result.run_id == inputs["processing_run_id"]
    assert result.error_stage is result.error_message is None
    assert result.run_record_path == record_path(inputs)
    text = result.run_record_path.read_text()
    record = json.loads(text)
    assert set(record) == {
        "schema_version",
        "run_id",
        "cik",
        "status",
        "started_at",
        "completed_at",
        "selection",
        "database_path",
        "company",
        "metadata",
        "dbt",
        "error",
    }
    assert record["schema_version"] == "1" and record["cik"] == "0000320193"
    assert record["status"] == status and record["error"] is None
    assert record["selection"] == {
        "forms": ["10-K", "10-Q", "10-k"],
        "filed_on_or_after": "2024-01-01",
        "filed_on_or_before": "2025-12-31",
        "limit": 2,
        "request_interval_seconds": 0.75,
        "dbt_timeout_seconds": 45.0,
    }
    started, completed = (
        datetime.fromisoformat(record[name]) for name in ("started_at", "completed_at")
    )
    assert started.utcoffset() == completed.utcoffset() == UTC.utcoffset(started)
    assert started <= completed
    assert record["database_path"] == str(pipeline.database_path)
    company = record["company"]
    assert company["status"] == status
    assert company["run_record_path"] == str(source.run_record_path)
    assert company["run_record_sha256"] == hashlib.sha256(original).hexdigest()
    assert source.pipeline_result is not None
    processing = source.pipeline_result.processing
    for name in (
        "selected_count",
        "complete_count",
        "partial_count",
        "skipped_count",
        "failed_count",
        "not_attempted_count",
        "usable_count",
    ):
        assert company["processing"][name] == getattr(processing, name)
    catalog = source.pipeline_result.catalog_result
    assert catalog is not None
    for name in (
        "active_filing_count",
        "failure_count",
        "facts_count",
        "fact_dimensions_count",
        "rejected_facts_count",
    ):
        assert company["catalog"][name] == getattr(catalog, name)
    assert "filing_results" not in company["processing"]
    assert record["metadata"]["status"] == "COMPLETE"
    assert record["dbt"]["project_directory"] == str(
        inputs["dbt_project_directory"].resolve()
    )
    assert record["dbt"]["profiles_directory"] == str(
        inputs["dbt_profiles_directory"].resolve()
    )
    assert record["dbt"]["requested_artifact_directory"] == str(
        inputs["dbt_artifact_directory"].resolve()
    )
    assert record["dbt"]["artifact_directory"] == str(pipeline.dbt_artifact_directory)
    assert record["dbt"]["node_status_counts"] == [["success", 1]]
    assert record["dbt"]["invocation_id"] == "fixture-invocation"
    assert source.run_record_path.read_bytes() == original
    assert (
        source.run_record_path.with_name("submissions.json").read_bytes()
        == b"exact source bytes"
    )
    assert inputs["user_agent"] not in text and "contact@example.org" not in text
    assert text.endswith("\n") and '\n  "schema_version"' in text
    assert not list(result.run_record_path.parent.glob("*.tmp"))


def test_empty_selection_and_skipped_stages_do_not_claim_artifacts(
    tmp_path: Path, inputs: dict[str, Any], coordinator: Mock
) -> None:
    company = company_result(tmp_path, outcomes=(), catalog_status="SKIPPED")
    company.run_record_path.parent.mkdir(parents=True, exist_ok=True)
    company.run_record_path.write_bytes(b"original empty record")
    pipeline = EndToEndPipelineResult(
        "COMPLETE",
        tmp_path / "catalog.duckdb",
        company,
        None,
        "SKIPPED",
        None,
        None,
        "No selected filings",
        "SKIPPED",
        None,
        None,
        "No selected filings",
        None,
    )
    coordinator.return_value = pipeline
    result = execute_end_to_end_pipeline_run(**inputs)
    record = json.loads(result.run_record_path.read_text())
    assert result.status == "COMPLETE"
    assert record["company"]["processing"]["selected_count"] == 0
    assert record["company"]["catalog"]["status"] == "SKIPPED"
    assert record["company"]["catalog"]["facts_count"] is None
    assert record["metadata"]["status"] == record["dbt"]["status"] == "SKIPPED"
    assert record["metadata"]["fiscal_results"] is None
    assert record["metadata"]["skip_reason"] == "No selected filings"
    for name in (
        "artifact_directory",
        "started_at",
        "return_code",
        "invocation_id",
        "node_status_counts",
        "stdout_path",
        "manifest_path",
        "run_results_path",
    ):
        assert record["dbt"][name] is None


def test_fiscal_order_provenance_issues_and_diagnostic_redaction(
    inputs: dict[str, Any], coordinator: Mock
) -> None:
    pipeline = coordinator.return_value
    metadata = pipeline.metadata_result
    assert metadata is not None and metadata.filing_metadata_result is not None
    first = metadata.fiscal_results[0]
    assert first.load_result is not None
    extracted = replace(
        first.load_result.metadata,
        status="PARTIAL",
        metadata_run_id="original-metadata-run",
        fiscal_year_focus=None,
        fiscal_period_focus="Q3",
        document_period_end_date=None,
        issues=(
            FiscalMetadataIssue(
                "fiscal_year_focus",
                "MISSING",
                inputs["user_agent"] + " another@example.net",
                ("occurrence-2", "occurrence-1"),
            ),
        ),
    )
    first = replace(
        first,
        outcome="ALREADY_EXISTS",
        load_result=replace(
            first.load_result, outcome="ALREADY_EXISTS", metadata=extracted
        ),
    )
    second_reference = replace(first.reference, accession_number="0000320193-25-000002")
    blocked = CompanyFiscalMetadataResult(
        second_reference, "NOT_ATTEMPTED", None, "FILING_METADATA", inputs["user_agent"]
    )
    filing = replace(
        metadata.filing_metadata_result,
        status="PARTIAL",
        issues=(
            FilingMetadataIssue(
                second_reference.cik,
                second_reference.accession_number,
                "METADATA_CONFLICT",
                inputs["user_agent"],
            ),
        ),
    )
    metadata = replace(
        metadata,
        status="PARTIAL",
        filing_metadata_result=filing,
        fiscal_results=(first, blocked),
    )
    coordinator.return_value = replace(
        pipeline, status="PARTIAL", metadata_status="PARTIAL", metadata_result=metadata
    )
    result = execute_end_to_end_pipeline_run(**inputs)
    record = json.loads(result.run_record_path.read_text())
    fiscal = record["metadata"]["fiscal_results"]
    assert [item["accession_number"] for item in fiscal] == [
        first.reference.accession_number,
        second_reference.accession_number,
    ]
    assert fiscal[0]["outcome"] == "ALREADY_EXISTS" and fiscal[0]["status"] == "PARTIAL"
    loaded = fiscal[0]["load_result"]
    assert loaded["metadata_run_id"] == "original-metadata-run"
    for name in (
        "selected_document_name",
        "selected_document_sha256",
        "submissions_sha256",
        "extraction_version",
    ):
        assert loaded[name] == getattr(extracted, name)
    assert loaded["fiscal_year_focus"] is None and loaded["fiscal_period_focus"] == "Q3"
    assert loaded["document_period_end_date"] is None
    assert loaded["issues"][0]["source_occurrence_ids"] == [
        "occurrence-2",
        "occurrence-1",
    ]
    assert loaded["issues"][0]["reason_code"] == "MISSING"
    assert (
        fiscal[1]["load_result"] is None
        and fiscal[1]["error_stage"] == "FILING_METADATA"
    )
    assert (
        record["metadata"]["filing_metadata"]["issues"][0]["reason_code"]
        == "METADATA_CONFLICT"
    )
    text = result.run_record_path.read_text()
    assert "[redacted]" in text and "@" not in text
    assert "occurrences" not in loaded and "context_xml" not in text


@pytest.mark.parametrize(
    "stage", ["company", "metadata", "dbt_result", "dbt_exception"]
)
def test_stage_failures_and_available_references(
    inputs: dict[str, Any], coordinator: Mock, stage: str
) -> None:
    original = coordinator.return_value
    if stage == "company":
        pipeline = replace(
            original,
            status="FAILED",
            company_run=None,
            company_error="publication failed contact@example.org",
            metadata_status="SKIPPED",
            metadata_result=None,
            metadata_skip_reason="company failed",
            dbt_status="SKIPPED",
            dbt_result=None,
            dbt_skip_reason="company failed",
            dbt_artifact_directory=None,
        )
    elif stage == "metadata":
        pipeline = replace(
            original,
            status="PARTIAL",
            metadata_status="FAILED",
            metadata_result=None,
            metadata_error="identity mismatch contact@example.org",
            dbt_status="SKIPPED",
            dbt_result=None,
            dbt_skip_reason="metadata failed",
            dbt_artifact_directory=None,
        )
    elif stage == "dbt_result":
        assert original.dbt_result is not None
        pipeline = replace(
            original,
            status="PARTIAL",
            dbt_status="FAILED",
            dbt_result=replace(
                original.dbt_result,
                status="FAILED",
                return_code=None,
                error_message="timed out contact@example.org",
            ),
        )
    else:
        pipeline = replace(
            original,
            status="PARTIAL",
            dbt_status="FAILED",
            dbt_result=None,
            dbt_error="launch failure contact@example.org",
        )
    coordinator.return_value = pipeline
    result = execute_end_to_end_pipeline_run(**inputs)
    record = json.loads(result.run_record_path.read_text())
    assert result.pipeline_result is pipeline and record["error"] is None
    assert result.pipeline_result is not None
    if stage == "company":
        assert record["company"]["status"] == "FAILED"
        assert (
            record["company"]["run_record_path"]
            is record["company"]["run_record_sha256"]
            is None
        )
        assert record["company"]["processing"] is record["company"]["catalog"] is None
    elif stage == "metadata":
        assert record["metadata"]["error"] == "identity mismatch [redacted]"
        assert record["metadata"]["filing_metadata"] is None
    elif stage == "dbt_result":
        assert record["dbt"]["error"] is None
        assert record["dbt"]["build_error"] == "timed out [redacted]"
        assert record["dbt"]["return_code"] is None
    else:
        assert record["dbt"]["error"] == "launch failure [redacted]"
        assert record["dbt"]["artifact_directory"] == str(
            result.pipeline_result.dbt_artifact_directory
        )
        assert record["dbt"]["stdout_path"] is None
    assert "contact@example.org" not in result.run_record_path.read_text()


def test_preflight_error_finalizes_failed_summary_with_unavailable_stages(
    inputs: dict[str, Any], coordinator: Mock
) -> None:
    coordinator.side_effect = EndToEndPipelineError(
        inputs["user_agent"] + " project unavailable"
    )
    result = execute_end_to_end_pipeline_run(**inputs)
    assert result.status == "FAILED" and result.pipeline_result is None
    assert (
        result.error_stage == "PREFLIGHT"
        and result.error_message == "[redacted] project unavailable"
    )
    record = json.loads(result.run_record_path.read_text())
    assert record["company"] is record["metadata"] is record["dbt"] is None
    assert record["database_path"] == str(inputs["database_path"].resolve())
    assert record["error"] == {"stage": "PREFLIGHT", "message": result.error_message}
    coordinator.assert_called_once()


def test_unexpected_coordinator_error_propagates(
    inputs: dict[str, Any], coordinator: Mock
) -> None:
    error = RuntimeError("unexpected programming failure")
    coordinator.side_effect = error
    with pytest.raises(RuntimeError) as raised:
        execute_end_to_end_pipeline_run(**inputs)
    assert raised.value is error
    assert not record_path(inputs).exists()


@pytest.mark.parametrize(
    "argument,value",
    [
        ("cik", "0"),
        ("cik", 320193),
        ("processing_run_id", ""),
        ("processing_run_id", "../unsafe"),
        ("processing_run_id", "with.dot"),
        ("processing_run_id", "é"),
        ("processing_run_id", "x" * 129),
        ("forms", "10-K"),
        ("forms", []),
        ("forms", [1]),
        ("filed_on_or_after", "2025-01-01"),
        ("filed_on_or_after", datetime(2025, 1, 1, tzinfo=UTC)),
        ("filed_on_or_after", date(2026, 1, 1)),
        ("limit", True),
        ("limit", 0),
        ("request_interval_seconds", 0.49),
        ("request_interval_seconds", True),
        ("request_interval_seconds", float("nan")),
        ("dbt_timeout_seconds", False),
        ("dbt_timeout_seconds", 0),
        ("dbt_timeout_seconds", float("inf")),
        ("dbt_timeout_seconds", "600"),
        ("user_agent", None),
    ],
)
def test_invalid_identity_and_serialization_inputs_never_execute(
    inputs: dict[str, Any], coordinator: Mock, argument: str, value: object
) -> None:
    inputs[argument] = value
    with pytest.raises(EndToEndRunError):
        execute_end_to_end_pipeline_run(**inputs)
    coordinator.assert_not_called()
    assert not inputs["end_to_end_run_directory"].exists()


@pytest.mark.parametrize(
    "argument",
    [
        "bronze_directory",
        "silver_directory",
        "database_path",
        "run_directory",
        "dbt_project_directory",
        "dbt_profiles_directory",
        "dbt_artifact_directory",
        "end_to_end_run_directory",
    ],
)
def test_invalid_path_types_never_execute(
    inputs: dict[str, Any], coordinator: Mock, argument: str
) -> None:
    inputs[argument] = str(inputs[argument])
    with pytest.raises(EndToEndRunError):
        execute_end_to_end_pipeline_run(**inputs)
    coordinator.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    ["record", "dangling_record", "root_symlink", "company_symlink", "root_file"],
)
def test_existing_records_and_unsafe_managed_paths_never_execute(
    tmp_path: Path, inputs: dict[str, Any], coordinator: Mock, kind: str
) -> None:
    root, final = inputs["end_to_end_run_directory"], record_path(inputs)
    target = tmp_path / "outside"
    target.mkdir()
    if kind == "root_symlink":
        root.symlink_to(target, target_is_directory=True)
    elif kind == "root_file":
        root.write_bytes(b"keep")
    else:
        root.mkdir()
        if kind == "company_symlink":
            final.parent.symlink_to(target, target_is_directory=True)
        else:
            final.parent.mkdir()
            if kind == "record":
                final.write_bytes(b"original final summary")
            else:
                final.symlink_to(target / "absent")
    with pytest.raises(EndToEndRunError):
        execute_end_to_end_pipeline_run(**inputs)
    coordinator.assert_not_called()
    assert list(target.iterdir()) == []
    if kind == "record":
        assert final.read_bytes() == b"original final summary"
    if kind == "dangling_record":
        assert final.is_symlink()


def test_system_alias_ancestor_is_allowed(
    tmp_path: Path, inputs: dict[str, Any], coordinator: Mock
) -> None:
    alias = tmp_path / "system alias"
    real = tmp_path / "real parent"
    real.mkdir()
    alias.symlink_to(real, target_is_directory=True)
    inputs["end_to_end_run_directory"] = alias / "managed root"
    result = execute_end_to_end_pipeline_run(**inputs)
    assert result.run_record_path.is_file()
    coordinator.assert_called_once()


@pytest.mark.parametrize("problem", ["missing", "directory", "symlink", "read_failure"])
def test_company_reference_failure_is_persistence_failure_with_retained_result(
    tmp_path: Path,
    inputs: dict[str, Any],
    coordinator: Mock,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    pipeline = coordinator.return_value
    path = pipeline.company_run.run_record_path
    if problem == "read_failure":
        monkeypatch.setattr(
            Path, "read_bytes", Mock(side_effect=OSError("unreadable evidence"))
        )
    else:
        path.unlink()
        if problem == "directory":
            path.mkdir()
        elif problem == "symlink":
            target = tmp_path / "original reference"
            target.write_bytes(b"keep source")
            path.symlink_to(target)
    with pytest.raises(EndToEndRunError) as raised:
        execute_end_to_end_pipeline_run(**inputs)
    assert raised.value.pipeline_result is pipeline
    assert raised.value.run_record_path == record_path(inputs)
    assert not record_path(inputs).exists()
    assert list(record_path(inputs).parent.iterdir()) == []


@pytest.mark.parametrize(
    "failure", ["fsync", "replace", "invalid_json", "changed_json", "competing_record"]
)
def test_publication_failures_retain_results_and_clean_only_owned_temporary_file(
    inputs: dict[str, Any],
    coordinator: Mock,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    final = record_path(inputs)
    final.parent.mkdir(parents=True)
    unrelated = final.parent / ".unrelated.tmp"
    unrelated.write_bytes(b"leave alone")
    original_read = Path.read_text
    if failure in {"fsync", "replace"}:
        monkeypatch.setattr(
            run.os, failure, Mock(side_effect=OSError("publication failure"))
        )
    else:

        def tamper(path: Path, *args: Any, **kwargs: Any) -> str:
            if path.name.startswith(f".{final.name}."):
                if failure == "invalid_json":
                    path.write_text("{")
                elif failure == "changed_json":
                    value = json.loads(original_read(path))
                    value["status"] = "changed"
                    path.write_text(json.dumps(value))
                elif failure == "competing_record":
                    final.write_bytes(b"another invocation")
            return original_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", tamper)
    with pytest.raises(EndToEndRunError) as raised:
        execute_end_to_end_pipeline_run(**inputs)
    assert raised.value.pipeline_result is coordinator.return_value
    assert raised.value.run_record_path == final
    assert unrelated.read_bytes() == b"leave alone"
    assert set(final.parent.iterdir()) == (
        {unrelated, final} if failure == "competing_record" else {unrelated}
    )
    if failure == "competing_record":
        assert final.read_bytes() == b"another invocation"


def test_prepared_directory_exists_before_execution_and_record_is_finalized_once(
    inputs: dict[str, Any], coordinator: Mock
) -> None:
    def execute(*args: Any, **kwargs: Any) -> EndToEndPipelineResult:
        assert record_path(inputs).parent.is_dir()
        assert not record_path(inputs).exists()
        assert list(record_path(inputs).parent.iterdir()) == []
        return cast(EndToEndPipelineResult, coordinator.return_value)

    coordinator.side_effect = execute
    execute_end_to_end_pipeline_run(**inputs)
    coordinator.reset_mock()
    with pytest.raises(EndToEndRunError, match="already exists"):
        execute_end_to_end_pipeline_run(**inputs)
    coordinator.assert_not_called()


def test_result_is_frozen_and_slotted(
    inputs: dict[str, Any], coordinator: Mock
) -> None:
    result = execute_end_to_end_pipeline_run(**inputs)
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.status = "FAILED"  # type: ignore[misc]


def test_earlier_company_database_and_dbt_evidence_remain_unchanged(
    inputs: dict[str, Any], coordinator: Mock
) -> None:
    pipeline = coordinator.return_value
    company, build = pipeline.company_run, pipeline.dbt_result
    assert company is not None and build is not None
    protected = [
        company.run_record_path,
        company.run_record_path.with_name("submissions.json"),
    ]
    for path in (
        pipeline.database_path,
        build.stdout_path,
        build.stderr_path,
        build.manifest_path,
        build.run_results_path,
        build.artifact_directory / "logs/dbt.log",
    ):
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"earlier output remains exact")
        protected.append(path)
    before = {path: path.read_bytes() for path in protected}
    execute_end_to_end_pipeline_run(**inputs)
    assert {path: path.read_bytes() for path in protected} == before


def test_nonfinite_summary_value_is_a_persistence_error_with_retained_result(
    inputs: dict[str, Any], coordinator: Mock
) -> None:
    pipeline = coordinator.return_value
    assert pipeline.dbt_result is not None
    invalid_build = replace(
        pipeline.dbt_result, node_status_counts=cast(Any, (("success", float("nan")),))
    )
    coordinator.return_value = replace(pipeline, dbt_result=invalid_build)
    with pytest.raises(EndToEndRunError) as raised:
        execute_end_to_end_pipeline_run(**inputs)
    assert raised.value.pipeline_result is coordinator.return_value
    assert raised.value.pipeline_result is not None
    assert raised.value.pipeline_result.status == "COMPLETE"
    assert not record_path(inputs).exists()
    assert list(record_path(inputs).parent.iterdir()) == []


def test_successful_publication_fsyncs_verifies_and_atomically_promotes(
    inputs: dict[str, Any], coordinator: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = record_path(inputs)
    original_replace = run.os.replace
    fsync = Mock(wraps=run.os.fsync)

    def promote(source: Path, destination: Path) -> None:
        assert destination == final and not destination.exists()
        assert source.parent == final.parent
        assert json.loads(source.read_text())["status"] == "COMPLETE"
        fsync.assert_called_once()
        original_replace(source, destination)

    replacement = Mock(side_effect=promote)
    monkeypatch.setattr(run.os, "fsync", fsync)
    monkeypatch.setattr(run.os, "replace", replacement)
    result = execute_end_to_end_pipeline_run(**inputs)
    assert result.run_record_path.is_file()
    replacement.assert_called_once()
