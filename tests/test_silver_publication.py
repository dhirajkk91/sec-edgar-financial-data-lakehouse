import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_silver_parquet import extraction, fact

from sec_edgar_lakehouse import (
    RejectedOccurrence,
    SilverPublicationError,
    publish_silver_extraction,
)
from sec_edgar_lakehouse import silver_publication as publication
from sec_edgar_lakehouse.silver_models import SilverExtraction


def prepared(tmp_path: Path) -> tuple[SilverExtraction, Path, Path]:
    value = extraction((fact("one", 1, Decimal(42)),))
    directory = (
        tmp_path
        / "Bronze space"
        / f"cik={value.reference.cik}"
        / f"accession={value.reference.accession_number}"
        / "manifests"
    )
    directory.mkdir(parents=True)
    manifest = directory / "run_id=bronze.json"
    manifest.write_text(
        json.dumps(
            {
                "cik": value.reference.cik,
                "accession_number": value.reference.accession_number,
                "status": "COMPLETE",
                "source_complete": True,
                "parser_ready": True,
                "files": [
                    {
                        "section": "data-file",
                        "document_name": value.selected_document_name,
                        "sha256": value.selected_document_sha256,
                        "status": "VERIFIED",
                    }
                ],
            }
        )
    )
    return value, manifest, tmp_path / "Silver space"


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def publish(
    value: SilverExtraction, manifest: Path, root: Path, run: str = "first"
) -> publication.SilverPublicationResult:
    return publish_silver_extraction(
        value,
        bronze_manifest_path=manifest,
        silver_directory=root,
        processing_run_id=run,
    )


def changed_source(value: SilverExtraction, manifest: Path) -> SilverExtraction:
    checksum = "b" * 64
    record = read(manifest)
    record["files"][0]["sha256"] = checksum
    manifest.write_text(json.dumps(record))
    return replace(
        value,
        selected_document_sha256=checksum,
        accepted_facts=tuple(
            replace(f, source_sha256=checksum) for f in value.accepted_facts
        ),
    )


def test_first_publication_and_active_rerun(tmp_path: Path) -> None:
    value, manifest, root = prepared(tmp_path)
    result = publish(value, manifest, root)
    assert result.outcome == "PUBLISHED" and result.active
    assert result.version_path.relative_to(result.active_path.parent).as_posix() == (
        f"versions/document={value.selected_document_name}/source_sha256={'a' * 64}/parser=1/schema=1"
    )
    metadata = read(result.publication_path)
    pointer = read(result.active_path)
    run = read(result.run_path)
    assert run["outcome"] == "PUBLISHED"
    assert metadata["accepted_count"] == 1
    assert (
        metadata["bronze_manifest_sha256"]
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    assert (
        pointer["publication_sha256"]
        == hashlib.sha256(result.publication_path.read_bytes()).hexdigest()
    )
    for record in metadata["files"]:
        path = result.version_path / record["filename"]
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert record["size_bytes"] == path.stat().st_size
    for text in (
        metadata["published_at"],
        pointer["activated_at"],
        run["started_at"],
        run["completed_at"],
    ):
        assert datetime.fromisoformat(text).utcoffset() == timedelta(0)
    with pytest.raises(FrozenInstanceError):
        result.active = False  # type: ignore[misc]
    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in result.active_path.parent.rglob("*")
        if p.is_file()
    }
    skipped = publish(value, manifest, root, "rerun")
    assert skipped.outcome == "SKIPPED" and skipped.active
    assert read(skipped.run_path)["skip_reason"] == "Existing version is active"
    assert all(
        (p.read_bytes(), p.stat().st_mtime_ns) == evidence
        for p, evidence in before.items()
    )
    assert {
        p for p in result.active_path.parent.rglob("*") if p.is_file()
    } - before.keys() == {skipped.run_path}


def test_partial_and_inactive_equivalent_version(tmp_path: Path) -> None:
    value, manifest, root = prepared(tmp_path)
    first = publish(value, manifest, root)
    changed = changed_source(value, manifest)
    rejection = RejectedOccurrence(
        "bad",
        2,
        "urn:example",
        "Bad",
        "oops",
        None,
        "USD",
        None,
        None,
        (),
        "INVALID_VALUE",
        "Bad number",
    )
    changed = replace(
        changed,
        status="PARTIAL",
        rejected_occurrences=(rejection,),
        rejected_count=1,
        candidate_count=2,
    )
    second = publish(changed, manifest, root, "second")
    assert (
        second.silver_status == "PARTIAL" and second.version_path != first.version_path
    )
    assert read(second.publication_path)["rejected_count"] == 1
    pointer = second.active_path.read_bytes()
    record = read(manifest)
    record["files"][0]["sha256"] = value.selected_document_sha256
    manifest.write_text(json.dumps(record))
    old = publish(value, manifest, root, "old-again")
    assert old.outcome == "SKIPPED" and not old.active
    assert second.active_path.read_bytes() == pointer


@pytest.mark.parametrize("constant", ["PARSER_VERSION", "SCHEMA_VERSION"])
def test_version_constants_change_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constant: str
) -> None:
    value, manifest, root = prepared(tmp_path)
    first = publish(value, manifest, root)
    monkeypatch.setattr(publication.parquet, constant, "2")
    second = publish(value, manifest, root, "new-version")
    assert first.version_path != second.version_path
    assert first.version_path.is_dir()


@pytest.mark.parametrize("point", ["staged", "activation"])
def test_failure_preserves_pointer_and_records_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    value, manifest, root = prepared(tmp_path)
    first = publish(value, manifest, root)
    pointer = first.active_path.read_bytes()
    changed = changed_source(value, manifest)
    if point == "staged":
        original = publication._verify_version

        def fail_stage(directory: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if ".staging" in directory.parts:
                raise ValueError("injected verification failure")
            return original(directory, *args, **kwargs)

        monkeypatch.setattr(publication, "_verify_version", fail_stage)
    else:
        original_replace = publication.os.replace

        def fail_activation(source: Any, destination: Any) -> None:
            if Path(destination).name == "active.json":
                raise OSError("injected activation failure")
            original_replace(source, destination)

        monkeypatch.setattr(publication.os, "replace", fail_activation)
    with pytest.raises(SilverPublicationError, match="injected") as caught:
        publish(changed, manifest, root, "failed")
    error = caught.value
    assert first.active_path.read_bytes() == pointer
    assert error.run_path is not None and read(error.run_path)["outcome"] == "FAILED"
    assert error.staging_path is not None and not error.staging_path.exists()
    assert error.version_path is not None
    assert error.version_path.exists() == (point == "activation")
    assert first.version_path.is_dir()
    assert not list(first.active_path.parent.glob(".publication-*.tmp"))


@pytest.mark.parametrize("mutation", ["checksum", "extra", "json"])
def test_corrupt_existing_version_is_never_replaced(
    tmp_path: Path, mutation: str
) -> None:
    value, manifest, root = prepared(tmp_path)
    first = publish(value, manifest, root)
    target = first.version_path / "facts.parquet"
    if mutation == "checksum":
        target.write_bytes(b"corrupt")
    elif mutation == "extra":
        (first.version_path / "unexpected").write_text("retain")
    else:
        first.publication_path.write_text("broken json")
    before = {p.name: p.read_bytes() for p in first.version_path.iterdir()}
    pointer = first.active_path.read_bytes()
    with pytest.raises(SilverPublicationError):
        publish(value, manifest, root, "corrupt-rerun")
    assert {p.name: p.read_bytes() for p in first.version_path.iterdir()} == before
    assert first.active_path.read_bytes() == pointer


@pytest.mark.parametrize("content", ["not json", '{"version_path":"../../outside"}'])
def test_invalid_active_pointer_is_preserved(tmp_path: Path, content: str) -> None:
    value, manifest, root = prepared(tmp_path)
    first = publish(value, manifest, root)
    first.active_path.write_text(content)
    with pytest.raises(SilverPublicationError):
        publish(value, manifest, root, "bad-pointer")
    assert first.active_path.read_text() == content


@pytest.mark.parametrize(
    "run_id", ["", ".", "..", "../escape", "back\\slash", "a\n", "x" * 129]
)
def test_unsafe_run_ids_create_nothing(tmp_path: Path, run_id: str) -> None:
    value, manifest, root = prepared(tmp_path)
    with pytest.raises(SilverPublicationError, match="Run ID"):
        publish(value, manifest, root, run_id)
    assert not root.exists()


def test_reused_run_id_leaves_everything_unchanged(tmp_path: Path) -> None:
    value, manifest, root = prepared(tmp_path)
    publish(value, manifest, root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(SilverPublicationError, match="already exists"):
        publish(value, manifest, root)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "field",
    ["cik", "parser_ready", "source_complete", "status", "document_name", "sha256"],
)
def test_bronze_mismatch_rejected_before_staging(tmp_path: Path, field: str) -> None:
    value, manifest, root = prepared(tmp_path)
    data = read(manifest)
    if field in {"document_name", "sha256"}:
        data["files"][0][field] = "wrong"
    else:
        data[field] = False
    manifest.write_text(json.dumps(data))
    with pytest.raises(SilverPublicationError):
        publish(value, manifest, root)
    assert not list(root.rglob(".staging"))


@pytest.mark.parametrize("missing_root", [False, True])
def test_symlinked_root_ancestor_uses_canonical_paths(
    tmp_path: Path, missing_root: bool
) -> None:
    value, manifest, root = prepared(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    try:
        root.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")
    supplied = root / "new" / "silver" if missing_root else root
    canonical = supplied.resolve()
    first = publish(value, manifest, supplied)
    second = publish(value, manifest, supplied, "second")
    assert first.outcome == "PUBLISHED"
    assert second.outcome == "SKIPPED"
    for result in (first, second):
        for path in (
            result.version_path,
            result.publication_path,
            result.active_path,
            result.run_path,
        ):
            assert path.is_relative_to(canonical)
            assert path == path.resolve()
            assert path.exists()


def test_root_must_be_a_directory(tmp_path: Path) -> None:
    value, manifest, root = prepared(tmp_path)
    root.write_text("keep")
    with pytest.raises(SilverPublicationError, match="Silver root"):
        publish(value, manifest, root)
    assert root.read_text() == "keep"


def test_encoded_document_and_invalid_counts(tmp_path: Path) -> None:
    value, manifest, root = prepared(tmp_path)
    name = "issuer space%name.xml"
    value = replace(
        value,
        selected_document_name=name,
        accepted_facts=tuple(
            replace(f, source_document_name=name) for f in value.accepted_facts
        ),
    )
    record = read(manifest)
    record["files"][0]["document_name"] = name
    manifest.write_text(json.dumps(record))
    result = publish(value, manifest, root)
    assert "document=issuer%20space%25name.xml" in result.version_path.parts
    assert read(result.publication_path)["source_document_name"] == name
    before = result.active_path.read_bytes()
    with pytest.raises(SilverPublicationError, match="count") as caught:
        publish(replace(value, accepted_count=2), manifest, root, "bad-count")
    assert caught.value.run_path is not None
    failure = read(caught.value.run_path)
    assert (
        failure["previous_active_version"] == read(result.active_path)["version_path"]
    )
    assert result.active_path.read_bytes() == before
    assert not (result.active_path.parent / ".staging" / "run_id=bad-count").exists()


def test_preexisting_staging_is_never_removed(tmp_path: Path) -> None:
    value, manifest, root = prepared(tmp_path)
    filing = (
        root
        / f"cik={value.reference.cik}"
        / f"accession={value.reference.accession_number}"
    )
    stage = filing / ".staging" / "run_id=first"
    stage.mkdir(parents=True)
    sentinel = stage / "keep"
    sentinel.write_text("keep")
    with pytest.raises(SilverPublicationError, match="Staging path already exists"):
        publish(value, manifest, root)
    assert sentinel.read_text() == "keep"


def test_cleanup_failure_reports_both_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, manifest, root = prepared(tmp_path)

    def fail_verification(*args: Any, **kwargs: Any) -> None:
        raise ValueError("verification broke")

    def fail_cleanup(*args: Any, **kwargs: Any) -> None:
        raise OSError("cleanup broke")

    monkeypatch.setattr(publication, "_verify_version", fail_verification)
    monkeypatch.setattr(publication.shutil, "rmtree", fail_cleanup)
    with pytest.raises(
        SilverPublicationError, match="verification broke.*cleanup broke"
    ) as caught:
        publish(value, manifest, root)
    assert caught.value.run_path is not None
    assert "cleanup broke" in read(caught.value.run_path)["failure_reason"]


@pytest.mark.parametrize("component", ["active.json", "runs", ".staging", "versions"])
def test_symlinked_filing_components_are_refused(
    tmp_path: Path, component: str
) -> None:
    value, manifest, root = prepared(tmp_path)
    filing = (
        root
        / f"cik={value.reference.cik}"
        / f"accession={value.reference.accession_number}"
    )
    filing.mkdir(parents=True)
    outside = tmp_path / "untouched"
    outside.mkdir()
    try:
        (filing / component).symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")
    with pytest.raises(SilverPublicationError, match="Symlink"):
        publish(value, manifest, root)
    assert not list(outside.iterdir())
