import hashlib
import json
import os
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from test_company_pipeline import arguments, catalog_result, processing_result

from sec_edgar_lakehouse import (
    CompanyPipelineError,
    CompanyPipelineResult,
    CompanyProcessingError,
    CompanyRunError,
    execute_company_pipeline_run,
)
from sec_edgar_lakehouse import company_run as run


def inputs(tmp_path: Path) -> dict[str, Any]:
    values = arguments(tmp_path)
    values["client"].close()
    values["run_directory"] = tmp_path / "runs"
    return values


def pipeline_result(
    status: str = "COMPLETE", catalog_status: str = "COMPLETE"
) -> CompanyPipelineResult:
    return CompanyPipelineResult(
        cast(Any, status),
        processing_result(
            status, ("COMPLETE", "PARTIAL", "SKIPPED", "FAILED", "NOT_ATTEMPTED")
        ),
        cast(Any, catalog_status),
        catalog_result(Path("catalog.duckdb"), catalog_status)
        if catalog_status in {"COMPLETE", "PARTIAL"}
        else None,
        "refresh failed" if catalog_status == "FAILED" else None,
    )


@pytest.mark.parametrize(
    "status,catalog_status",
    [
        ("COMPLETE", "COMPLETE"),
        ("PARTIAL", "PARTIAL"),
        ("PARTIAL", "FAILED"),
        ("FAILED", "SKIPPED"),
    ],
)
def test_records_results_and_exact_forwarding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    catalog_status: str,
) -> None:
    pipeline = pipeline_result(status, catalog_status)
    first = replace(
        pipeline.processing.filing_results[0],
        bronze_status="COMPLETE",
        bronze_path=tmp_path / "bronze",
        bronze_manifest_path=tmp_path / "manifest.json",
        silver_status="PARTIAL",
        silver_outcome="PUBLISHED",
        silver_version_path=tmp_path / "version",
        error_stage="SILVER",
        error_message="some facts rejected",
        filing=replace(pipeline.processing.filing_results[0].filing, report_date=None),
    )
    pipeline = replace(
        pipeline,
        processing=replace(
            pipeline.processing,
            filing_results=(first, *pipeline.processing.filing_results[1:]),
        ),
    )
    execute = Mock(return_value=pipeline)
    monkeypatch.setattr(run, "run_company_pipeline", execute)
    values = inputs(tmp_path)
    result = execute_company_pipeline_run(**values)
    forwarded = {
        key: value
        for key, value in values.items()
        if key not in {"cik", "run_directory"}
    }
    execute.assert_called_once_with(values["cik"], **forwarded)
    assert result.pipeline_result is pipeline
    assert result.status == status
    assert result.error_stage is result.error_message is None
    assert (
        result.run_record_path
        == tmp_path / "runs/cik=0000320193/run_id=company-run/run.json"
    )
    content = result.run_record_path.read_text()
    record = json.loads(content)
    assert content.endswith("\n") and '\n  "schema_version"' in content
    assert record["schema_version"] == "1"
    assert record["cik"] == "0000320193" and record["status"] == status
    assert record["error"] is None
    assert record["selection"] == {
        "forms": ["10-K", "10-Q"],
        "filed_on_or_after": "2024-01-01",
        "filed_on_or_before": "2025-12-31",
        "limit": 8,
    }
    start, end = (
        datetime.fromisoformat(record[key]) for key in ("started_at", "completed_at")
    )
    assert start.utcoffset() == end.utcoffset() == timedelta(0)
    assert start <= end
    processing = record["processing"]
    assert processing["company_name"] == "Apple Inc."
    assert processing["selected_count"] == 5 and processing["usable_count"] == 3
    for name in ("complete", "partial", "skipped", "failed", "not_attempted"):
        assert processing[f"{name}_count"] == 1
    filings = processing["filing_results"]
    assert [item["outcome"] for item in filings] == [
        "COMPLETE",
        "PARTIAL",
        "SKIPPED",
        "FAILED",
        "NOT_ATTEMPTED",
    ]
    assert [item["accession_number"] for item in filings] == [
        item.filing.reference.accession_number
        for item in pipeline.processing.filing_results
    ]
    assert filings[0] == {
        "cik": "0000320193",
        "accession_number": "0000320193-25-000001",
        "form": "10-K",
        "filing_date": "2025-01-01",
        "report_date": None,
        "primary_document": "filing-1.htm",
        "outcome": "COMPLETE",
        "bronze_status": "COMPLETE",
        "bronze_path": str(tmp_path / "bronze"),
        "bronze_manifest_path": str(tmp_path / "manifest.json"),
        "silver_status": "PARTIAL",
        "silver_outcome": "PUBLISHED",
        "silver_version_path": str(tmp_path / "version"),
        "error_stage": "SILVER",
        "error_message": "some facts rejected",
    }
    assert filings[1]["report_date"] == "2025-01-02"
    catalog = record["catalog"]
    assert catalog["status"] == catalog_status
    if pipeline.catalog_result:
        assert catalog == {
            "status": catalog_status,
            "database_path": "catalog.duckdb",
            "active_filing_count": 1,
            "failure_count": 0,
            "facts_count": 3,
            "fact_dimensions_count": 2,
            "rejected_facts_count": 1,
            "error": None,
        }
    else:
        assert all(
            value is None
            for key, value in catalog.items()
            if key not in {"status", "error"}
        )
        assert catalog["error"] == pipeline.catalog_error
    assert values["user_agent"] not in content and "contact@example.org" not in content
    assert "submissions_content" not in content
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.status = "FAILED"  # type: ignore[misc]


@pytest.mark.parametrize(
    "error,stage",
    [
        (CompanyPipelineError("bad database"), "PIPELINE"),
        (CompanyProcessingError("discovery failed"), "PROCESSING"),
    ],
)
def test_expected_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, stage: str
) -> None:
    execute = Mock(side_effect=error)
    monkeypatch.setattr(run, "run_company_pipeline", execute)
    result = execute_company_pipeline_run(**inputs(tmp_path))
    execute.assert_called_once()
    assert result.status == "FAILED" and result.pipeline_result is None
    assert result.error_stage == stage and result.error_message == str(error)
    record = json.loads(result.run_record_path.read_text())
    assert record["error"] == {"stage": stage, "message": str(error)}
    assert record["submissions_evidence"] is None
    assert list(result.run_record_path.parent.iterdir()) == [result.run_record_path]
    assert record["processing"] is None
    assert record["catalog"]["facts_count"] is None


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        " ",
        "a b",
        "a/b",
        "a\\b",
        ".",
        "..",
        "a.b",
        "\n",
        "%2e%2e",
        "/tmp/run",
        "https://run",
        "é",
        "x" * 129,
    ],
)
def test_unsafe_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_id: str
) -> None:
    execute = Mock()
    monkeypatch.setattr(run, "run_company_pipeline", execute)
    values = inputs(tmp_path)
    values["processing_run_id"] = run_id
    with pytest.raises(CompanyRunError):
        execute_company_pipeline_run(**values)
    execute.assert_not_called()


@pytest.mark.parametrize("cik", ["0", "abc", "12345678901", "１", None])
def test_invalid_cik(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cik: Any) -> None:
    execute = Mock()
    monkeypatch.setattr(run, "run_company_pipeline", execute)
    values = inputs(tmp_path)
    values["cik"] = cik
    with pytest.raises(CompanyRunError):
        execute_company_pipeline_run(**values)
    execute.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        "root_file",
        "company_file",
        "existing",
        "root_link",
        "company_link",
        "record_link",
        "wrong_type",
    ],
)
def test_destinations_rejected_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    values = inputs(tmp_path)
    root = values["run_directory"]
    root.mkdir()
    company = root / "cik=0000320193"
    company.mkdir()
    final = company / "run_id=company-run"
    target = tmp_path / "target"
    target.mkdir()
    if kind == "wrong_type":
        values["run_directory"] = "runs"
    elif kind == "existing":
        final.mkdir()
        (final / "run.json").write_text("preserve")
    elif kind.endswith("file"):
        path = root if kind == "root_file" else company
        if path == root:
            company.rmdir()
        path.rmdir()
        path.write_text("preserve")
    else:
        path = {"root_link": root, "company_link": company, "record_link": final}[kind]
        if path == root:
            company.rmdir()
        if path.exists():
            path.rmdir()
        try:
            path.symlink_to(target if path != final else tmp_path / "missing")
        except OSError:
            pytest.skip("Symlinks unavailable")
    execute = Mock()
    monkeypatch.setattr(run, "run_company_pipeline", execute)
    with pytest.raises(CompanyRunError):
        execute_company_pipeline_run(**values)
    execute.assert_not_called()
    if kind == "existing":
        assert (final / "run.json").read_text() == "preserve"


def test_atomic_promotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run, "run_company_pipeline", Mock(return_value=pipeline_result())
    )
    replace_file = os.replace
    sync = Mock(wraps=os.fsync)
    monkeypatch.setattr(run.os, "fsync", sync)

    def promote(source: Path, destination: Path) -> None:
        assert source.parent == destination.parent and source != destination
        assert not destination.exists()
        if source.is_dir():
            assert {item.name for item in source.iterdir()} == {
                "run.json",
                "submissions.json",
            }
            assert json.loads((source / "run.json").read_text())["status"] == "COMPLETE"
            assert sync.call_count == 2
        else:
            assert sync.call_count >= 1
        replace_file(source, destination)

    promotion = Mock(side_effect=promote)
    monkeypatch.setattr(run.os, "replace", promotion)
    result = execute_company_pipeline_run(**inputs(tmp_path))
    assert promotion.call_count == 3
    assert {item.name for item in result.run_record_path.parent.iterdir()} == {
        "run.json",
        "submissions.json",
    }


@pytest.mark.parametrize("failure", ["fsync", "replace", "competing_record"])
def test_persistence_failure_preserves_outputs_and_cleans_only_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    values = inputs(tmp_path)
    company = values["run_directory"] / "cik=0000320193"
    company.mkdir(parents=True)
    unrelated = company / ".someone-else.tmp"
    unrelated.write_text("keep")
    outputs = [
        values[key] for key in ("bronze_directory", "silver_directory", "database_path")
    ]

    def pipeline(*args: Any, **kwargs: Any) -> CompanyPipelineResult:
        for output in outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("pipeline output")
        return pipeline_result()

    monkeypatch.setattr(run, "run_company_pipeline", pipeline)
    final = company / "run_id=company-run"
    if failure == "competing_record":

        def competing_record(fd: int) -> None:
            final.mkdir(exist_ok=True)
            (final / "run.json").write_text("other writer")

        monkeypatch.setattr(run.os, "fsync", competing_record)
    else:
        monkeypatch.setattr(run.os, failure, Mock(side_effect=OSError("disk failure")))
    with pytest.raises(CompanyRunError):
        execute_company_pipeline_run(**values)
    assert all(output.read_text() == "pipeline output" for output in outputs)
    assert unrelated.read_text() == "keep"
    assert set(company.iterdir()) == (
        {unrelated, final} if failure == "competing_record" else {unrelated}
    )
    if failure == "competing_record":
        assert (final / "run.json").read_text() == "other writer"


def test_programming_errors_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        run, "run_company_pipeline", Mock(side_effect=RuntimeError("bug"))
    )
    with pytest.raises(RuntimeError, match="bug"):
        execute_company_pipeline_run(**inputs(tmp_path))
    assert not list((tmp_path / "runs").rglob("*.json"))


@pytest.mark.parametrize("stage", ["execution", "filing", "catalog"])
def test_errors_do_not_persist_contact_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    values = inputs(tmp_path)
    message = f"Rejected {values['user_agent']} or other@example.org"
    pipeline = pipeline_result("PARTIAL", "FAILED")
    if stage == "execution":
        execute = Mock(side_effect=CompanyProcessingError(message))
    else:
        if stage == "catalog":
            pipeline = replace(pipeline, catalog_error=message)
        else:
            item = replace(pipeline.processing.filing_results[0], error_message=message)
            pipeline = replace(
                pipeline,
                processing=replace(pipeline.processing, filing_results=(item,)),
            )
        execute = Mock(return_value=pipeline)
    monkeypatch.setattr(run, "run_company_pipeline", execute)
    result = execute_company_pipeline_run(**values)
    content = result.run_record_path.read_text()
    assert values["user_agent"] not in content
    assert "@example.org" not in content
    assert "[redacted]" in content


def test_real_input_error_is_recorded_without_network(tmp_path: Path) -> None:
    values = inputs(tmp_path)
    values["limit"] = 0
    result = execute_company_pipeline_run(**values)
    assert result.status == "FAILED"
    assert result.error_stage == "PROCESSING"
    assert result.error_message == "limit must be a positive integer"
    assert result.run_record_path.is_file()


@pytest.mark.parametrize(
    "status,catalog_status,outcomes",
    [
        ("COMPLETE", "COMPLETE", ("COMPLETE",)),
        ("PARTIAL", "PARTIAL", ("PARTIAL",)),
        ("PARTIAL", "FAILED", ("COMPLETE",)),
        ("FAILED", "SKIPPED", ("FAILED",)),
        ("COMPLETE", "COMPLETE", ("SKIPPED",)),
        ("COMPLETE", "SKIPPED", ()),
    ],
)
@pytest.mark.parametrize("source_contact", [False, True])
def test_exact_discovery_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    catalog_status: str,
    outcomes: tuple[str, ...],
    source_contact: bool,
) -> None:
    values = inputs(tmp_path)
    content = b'  { "name": "Apple", "recent": [] }\r\n\n'
    if source_contact:
        content = b'{"source_text": "' + values["user_agent"].encode() + b'"}\r\n'
    processing = processing_result(status, outcomes)
    discovery = replace(processing.discovery, submissions_content=content)
    pipeline = replace(
        pipeline_result(status, catalog_status),
        processing=replace(processing, discovery=discovery),
    )
    final = values["run_directory"] / "cik=0000320193/run_id=company-run"

    def execute(*args: Any, **kwargs: Any) -> CompanyPipelineResult:
        assert not final.exists()
        return pipeline

    monkeypatch.setattr(run, "run_company_pipeline", execute)
    result = execute_company_pipeline_run(**values)
    record = json.loads(result.run_record_path.read_text())
    assert set(record) == {
        "schema_version",
        "run_id",
        "cik",
        "status",
        "started_at",
        "completed_at",
        "selection",
        "submissions_evidence",
        "processing",
        "catalog",
        "error",
    }
    assert record["submissions_evidence"] == {
        "path": "submissions.json",
        "url": discovery.submissions_url,
        "retrieved_at": discovery.retrieved_at.isoformat(),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    assert (final / "submissions.json").read_bytes() == content
    assert {item.name for item in final.iterdir()} == {"run.json", "submissions.json"}
    assert list(final.parent.iterdir()) == [final]
    assert values["user_agent"] not in result.run_record_path.read_text()
    assert "contact@example.org" not in result.run_record_path.read_text()
    if not source_contact:
        assert b"contact@example.org" not in (final / "submissions.json").read_bytes()


@pytest.mark.parametrize(
    "failure",
    [
        "submissions_size",
        "submissions_hash",
        "missing_submissions",
        "invalid_json",
        "changed_json",
        "extra_file",
        "extra_directory",
        "promotion",
    ],
)
def test_verification_blocks_publication_and_preserves_other_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    values = inputs(tmp_path)
    company = values["run_directory"] / "cik=0000320193"
    company.mkdir(parents=True)
    unrelated = company / ".another-attempt"
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"untouched")
    outputs = [
        values[key] for key in ("bronze_directory", "silver_directory", "database_path")
    ]

    def execute(*args: Any, **kwargs: Any) -> CompanyPipelineResult:
        for output in outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("pipeline output")
        return pipeline_result()

    monkeypatch.setattr(run, "run_company_pipeline", execute)
    verify = run._verify_evidence

    def tamper(staging: Path, record: dict[str, Any]) -> None:
        submissions = staging / "submissions.json"
        run_record = staging / "run.json"
        if failure == "submissions_size":
            submissions.write_bytes(b"longer")
        elif failure == "submissions_hash":
            submissions.write_bytes(b"[]")
        elif failure == "missing_submissions":
            submissions.unlink()
        elif failure == "invalid_json":
            run_record.write_text("{")
        elif failure == "changed_json":
            run_record.write_text("{}")
        elif failure == "extra_file":
            (staging / "unexpected.tmp").touch()
        elif failure == "extra_directory":
            (staging / "unexpected").mkdir()
        verify(staging, record)

    monkeypatch.setattr(run, "_verify_evidence", tamper)
    if failure == "promotion":
        promote = os.replace

        def fail_directory(source: Path, destination: Path) -> None:
            if source.is_dir():
                raise OSError("directory promotion failed")
            promote(source, destination)

        monkeypatch.setattr(run.os, "replace", fail_directory)
    with pytest.raises(CompanyRunError):
        execute_company_pipeline_run(**values)
    assert list(company.iterdir()) == [unrelated]
    assert (unrelated / "keep").read_bytes() == b"untouched"
    assert all(output.read_text() == "pipeline output" for output in outputs)
