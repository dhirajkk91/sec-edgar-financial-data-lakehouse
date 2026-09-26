"""Publish immutable local Silver versions and switch their active pointer."""

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

import duckdb

from sec_edgar_lakehouse import silver_parquet as parquet
from sec_edgar_lakehouse.silver_models import SilverExtraction, SilverStatus

_FILES = {
    "facts.parquet": (parquet._FACTS_SCHEMA, "accepted_count"),
    "fact_dimensions.parquet": (parquet._DIMENSIONS_SCHEMA, "dimension_count"),
    "rejected_facts.parquet": (parquet._REJECTIONS_SCHEMA, "rejected_count"),
}


class SilverPublicationError(Exception):
    """A Silver attempt failed; attached paths identify any retained evidence."""

    def __init__(
        self,
        message: str,
        *,
        run_path: Path | None = None,
        staging_path: Path | None = None,
        version_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.run_path = run_path
        self.staging_path = staging_path
        self.version_path = version_path


@dataclass(frozen=True, slots=True)
class SilverPublicationResult:
    outcome: Literal["PUBLISHED", "SKIPPED"]
    silver_status: SilverStatus
    version_path: Path
    publication_path: Path
    active_path: Path
    run_path: Path
    active: bool


def publish_silver_extraction(
    extraction: SilverExtraction,
    *,
    bronze_manifest_path: Path,
    silver_directory: Path,
    processing_run_id: str,
) -> SilverPublicationResult:
    """Verify, publish, and activate a local version, or record an equivalent skip."""
    if not isinstance(extraction, SilverExtraction):
        raise SilverPublicationError("Extraction must be a SilverExtraction")
    if not isinstance(bronze_manifest_path, Path) or not isinstance(
        silver_directory, Path
    ):
        raise SilverPublicationError(
            "Bronze manifest and Silver directory must be pathlib.Path values"
        )
    _safe_id(processing_run_id)
    root = _canonical_root(silver_directory)
    reference = extraction.reference
    filing = root / f"cik={reference.cik}" / f"accession={reference.accession_number}"
    run_path = filing / "runs" / f"run_id={processing_run_id}.json"
    active_path = filing / "active.json"
    _no_symlinks(filing, root)
    _no_symlinks(run_path, root)
    if run_path.exists():
        raise SilverPublicationError(
            f"Run record already exists: {run_path}", run_path=run_path
        )
    # Only a matching canonical Bronze manifest establishes a trusted filing.
    manifest_bytes, manifest = _bronze_identity(bronze_manifest_path, extraction)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    started = _now()
    previous: str | None = None
    resulting: str | None = None
    version: Path | None = None
    staging: Path | None = None
    owned_staging = False
    relative: str | None = None
    run: dict[str, Any] = {
        **_identity(extraction),
        "processing_run_id": processing_run_id,
        "started_at": started,
        "bronze_manifest_path": str(bronze_manifest_path.absolute()),
        "bronze_manifest_sha256": manifest_sha,
    }
    try:
        previous = _read_active(filing, extraction, root=root)
        resulting = previous
        _validate_source(extraction, manifest)
        relative = _version_relative(_identity(extraction))
        version = _contained(filing, relative, root=root)
        staging = filing / ".staging" / f"run_id={processing_run_id}"
        _no_symlinks(staging, root)
        if staging.exists():
            raise SilverPublicationError(f"Staging path already exists: {staging}")
        if version.exists():
            publication = _verify_version(version, relative, extraction, root=root)
            outcome: Literal["PUBLISHED", "SKIPPED"] = "SKIPPED"
            reason: str | None = (
                "Existing version is active"
                if previous == relative
                else "Existing version is inactive; activation unchanged"
            )
            if previous != relative:
                recovered_run = _recoverable_run(
                    filing,
                    relative,
                    publication,
                    extraction,
                    current_active_version=previous,
                    root=root,
                )
                if recovered_run is not None:
                    run["recovered_version_from_run_id"] = recovered_run
                    outcome = "PUBLISHED"
                    reason = None
        else:
            staging.parent.mkdir(parents=True, exist_ok=True)
            staging.mkdir()
            owned_staging = True
            working = staging / "version"
            parquet.write_silver_parquet(
                extraction, working, processing_run_id=processing_run_id
            )
            publication = {
                **_identity(extraction),
                "publication_format_version": "1",
                "processing_run_id": processing_run_id,
                "published_at": _now(),
                "bronze_manifest_path": str(bronze_manifest_path.absolute()),
                "bronze_manifest_sha256": manifest_sha,
                "version_path": relative,
                **_counts(extraction),
                "files": _file_evidence(working),
            }
            _write_json(working / "publication.json", publication, root=root)
            _verify_version(working, relative, extraction, root=root)
            _no_symlinks(version, root)
            version.parent.mkdir(parents=True, exist_ok=True)
            if version.exists():
                raise SilverPublicationError(f"Version already exists: {version}")
            if working.stat().st_dev != version.parent.stat().st_dev:
                raise SilverPublicationError(
                    "Staging and version must be on the same filesystem"
                )
            os.rename(working, version)
            _verify_version(version, relative, extraction, root=root)
            staging.rmdir()
            owned_staging = False
            outcome = "PUBLISHED"
            reason = None
        if outcome == "PUBLISHED":
            pointer = {
                **_identity(extraction),
                "processing_run_id": processing_run_id,
                "activated_at": _now(),
                "version_path": relative,
                "publication_path": f"{relative}/publication.json",
                "publication_sha256": _sha(version / "publication.json"),
            }
            # The version is complete before readers can see it through this pointer.
            _write_json(active_path, pointer, replace=True, root=root)
            resulting = relative
        run.update(
            completed_at=_now(),
            outcome=outcome,
            intended_version_path=relative,
            previous_active_version=previous,
            resulting_active_version=resulting,
            active=resulting == relative,
        )
        if reason:
            run["skip_reason"] = reason
        _write_json(run_path, run, root=root)
        return SilverPublicationResult(
            outcome,
            extraction.status,
            version,
            version / "publication.json",
            active_path,
            run_path,
            resulting == relative,
        )
    except Exception as exc:
        errors = [str(exc)]
        if owned_staging and staging is not None:
            try:
                _no_symlinks(staging, root)
                shutil.rmtree(staging)
            except OSError as cleanup:
                errors.append(f"Staging cleanup failed: {cleanup}")
            except SilverPublicationError as cleanup:
                errors.append(f"Staging cleanup failed: {cleanup}")
        run.update(
            completed_at=_now(),
            outcome="FAILED",
            intended_version_path=relative,
            previous_active_version=previous,
            resulting_active_version=resulting,
            active=relative is not None and resulting == relative,
            failure_reason="; ".join(errors),
        )
        try:
            _write_json(run_path, run, root=root)
        except (OSError, ValueError, TypeError, SilverPublicationError) as recording:
            errors.append(f"Failed run record could not be written: {recording}")
        raise SilverPublicationError(
            "; ".join(errors),
            run_path=run_path,
            staging_path=staging,
            version_path=version,
        ) from exc


def _recoverable_run(
    filing: Path,
    relative: str,
    publication: dict[str, Any],
    extraction: SilverExtraction,
    *,
    current_active_version: str | None,
    root: Path,
) -> str | None:
    # Only failed activation evidence can distinguish an orphan from an old version.
    original_id = publication["processing_run_id"]
    _safe_id(original_id)
    path = _contained(filing, f"runs/run_id={original_id}.json", root=root)
    try:
        record = _read_json(path, root=root)
        for key in (
            *_identity(extraction),
            "bronze_manifest_path",
            "bronze_manifest_sha256",
        ):
            if record.get(key) != publication.get(key):
                return None
        for key in ("started_at", "completed_at"):
            _utc_timestamp(record.get(key))
        for key in ("previous_active_version", "resulting_active_version"):
            if key not in record:
                return None
            if record[key] is not None:
                _contained(filing, record[key], root=root)
        if (
            record.get("processing_run_id") != original_id
            or record.get("outcome") != "FAILED"
            or record.get("intended_version_path") != relative
            or record["resulting_active_version"] == relative
            or record["resulting_active_version"] != record["previous_active_version"]
            or record["resulting_active_version"] != current_active_version
            or record.get("active") is not False
            or not isinstance(record.get("failure_reason"), str)
            or not record["failure_reason"].strip()
        ):
            return None
    except OSError, ValueError, SilverPublicationError:
        return None
    return str(original_id)


def _safe_id(value: object) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) is None
        or value in {".", ".."}
    ):
        raise SilverPublicationError(
            "Run ID must be 1-128 letters, numbers, periods, underscores or hyphens, excluding . and .."
        )


def _canonical_root(path: Path) -> Path:
    try:
        # System aliases such as macOS /var are outside our managed layout.
        root = path.resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise SilverPublicationError(f"Silver root must be a directory: {path}")
        return root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SilverPublicationError(
            f"Cannot prepare Silver root {path}: {exc}"
        ) from exc


def _no_symlinks(path: Path, root: Path | None = None) -> None:
    if ".." in path.parts:
        raise SilverPublicationError(f"Traversal component in path: {path}")
    if root is not None and not path.is_relative_to(root):
        raise SilverPublicationError(f"Path is outside Silver root: {path}")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise SilverPublicationError(f"Symlinked path is not allowed: {component}")
        if component == root:
            break


def _contained(filing: Path, value: object, *, root: Path) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SilverPublicationError("Invalid relative publication path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != value:
        raise SilverPublicationError(f"Unsafe relative publication path: {value!r}")
    path = filing.joinpath(*relative.parts)
    _no_symlinks(path, root)
    return path


def _read_json(path: Path, *, root: Path) -> dict[str, Any]:
    _no_symlinks(path, root)
    if not path.is_file():
        raise SilverPublicationError(f"Expected regular JSON file: {path}")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise SilverPublicationError(f"Expected JSON object: {path}")
    return value


def _bronze_identity(
    path: Path, extraction: SilverExtraction
) -> tuple[bytes, dict[str, Any]]:
    try:
        _no_symlinks(path)
        if not path.is_file() or path.parent.name != "manifests":
            raise SilverPublicationError(
                "Bronze manifest must be a regular file directly inside manifests"
            )
        content = path.read_bytes()
        manifest = json.loads(content)
        ref = extraction.reference
        if (
            not isinstance(manifest, dict)
            or manifest.get("cik") != ref.cik
            or manifest.get("accession_number") != ref.accession_number
        ):
            raise SilverPublicationError("Bronze manifest filing identity mismatch")
        if (
            path.parent.parent.name != f"accession={ref.accession_number}"
            or path.parent.parent.parent.name != f"cik={ref.cik}"
        ):
            raise SilverPublicationError(
                "Bronze manifest is outside its canonical filing directory"
            )
        return content, manifest
    except (OSError, ValueError) as exc:
        raise SilverPublicationError(f"Cannot read Bronze manifest: {exc}") from exc


def _validate_source(extraction: SilverExtraction, manifest: dict[str, Any]) -> None:
    parquet._validate_extraction(extraction)
    if extraction.status not in ("COMPLETE", "PARTIAL") or not any(
        not fact.is_nil for fact in extraction.accepted_facts
    ):
        raise SilverPublicationError(
            "Extraction requires COMPLETE/PARTIAL and a non-nil accepted fact"
        )
    if (extraction.status == "PARTIAL") != bool(extraction.rejected_count):
        raise SilverPublicationError("Extraction status and rejection count disagree")
    if (
        not isinstance(extraction.selected_document_name, str)
        or not extraction.selected_document_name
    ):
        raise SilverPublicationError("Source document name must be non-empty")
    if re.fullmatch(r"[0-9a-f]{64}", extraction.selected_document_sha256) is None:
        raise SilverPublicationError("Source SHA-256 is invalid")
    if (
        manifest.get("status") not in ("COMPLETE", "PARTIAL")
        or manifest.get("source_complete") is not True
        or manifest.get("parser_ready") is not True
    ):
        raise SilverPublicationError(
            "Bronze manifest is not source-complete and parser-ready"
        )
    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise SilverPublicationError("Invalid Bronze manifest files")
    matches = [
        item
        for item in files
        if item.get("section") == "data-file"
        and item.get("document_name") == extraction.selected_document_name
    ]
    if (
        len(matches) != 1
        or matches[0].get("status") != "VERIFIED"
        or matches[0].get("sha256") != extraction.selected_document_sha256
    ):
        raise SilverPublicationError("Bronze selected document or checksum mismatch")


def _identity(extraction: SilverExtraction) -> dict[str, Any]:
    return {
        "cik": extraction.reference.cik,
        "accession_number": extraction.reference.accession_number,
        "source_document_name": extraction.selected_document_name,
        "source_sha256": extraction.selected_document_sha256,
        "parser_version": parquet.PARSER_VERSION,
        "schema_version": parquet.SCHEMA_VERSION,
        "silver_status": extraction.status,
    }


def _version_relative(identity: dict[str, Any]) -> str:
    return (
        f"versions/document={quote(identity['source_document_name'], safe='')}/"
        f"source_sha256={identity['source_sha256']}/parser={identity['parser_version']}/"
        f"schema={identity['schema_version']}"
    )


def _counts(extraction: SilverExtraction) -> dict[str, int]:
    return {
        "accepted_count": extraction.accepted_count,
        "dimension_count": len(extraction.dimensions),
        "rejected_count": extraction.rejected_count,
        "candidate_count": extraction.candidate_count,
    }


def _file_evidence(directory: Path) -> list[dict[str, Any]]:
    records = []
    with duckdb.connect(":memory:") as connection:
        for name, (schema, _) in _FILES.items():
            path = directory / name
            rows = connection.execute(
                "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
                [str(path)],
            ).fetchall()
            records.append(
                {
                    "filename": name,
                    "row_count": rows[0][0],
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha(path),
                    "schema": [list(column) for column in schema],
                }
            )
    return records


def _verify_version(
    directory: Path,
    relative: str,
    extraction: SilverExtraction | None = None,
    *,
    root: Path,
) -> dict[str, Any]:
    _no_symlinks(directory, root)
    publication = _read_json(directory / "publication.json", root=root)
    if (
        publication.get("publication_format_version") != "1"
        or publication.get("version_path") != relative
        or _version_relative(publication) != relative
    ):
        raise SilverPublicationError("Publication version identity or path mismatch")
    _utc_timestamp(publication.get("published_at"))
    _safe_id(publication.get("processing_run_id"))
    for key in ("source_sha256", "bronze_manifest_sha256"):
        checksum = publication.get(key)
        if (
            not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
        ):
            raise SilverPublicationError(f"Invalid publication checksum: {key}")
    lineage = publication.get("bronze_manifest_path")
    if not isinstance(lineage, str) or not Path(lineage).is_absolute():
        raise SilverPublicationError("Invalid publication Bronze manifest path")
    if extraction is not None:
        for key, expected in {**_identity(extraction), **_counts(extraction)}.items():
            if publication.get(key) != expected:
                raise SilverPublicationError(f"Publication {key} mismatch")
    if publication.get("silver_status") not in ("COMPLETE", "PARTIAL"):
        raise SilverPublicationError("Invalid publication status")
    for key in (
        "accepted_count",
        "rejected_count",
        "candidate_count",
        "dimension_count",
    ):
        if type(publication.get(key)) is not int or publication[key] < 0:
            raise SilverPublicationError(f"Invalid publication count: {key}")
    if (
        publication["accepted_count"] + publication["rejected_count"]
        != publication["candidate_count"]
    ):
        raise SilverPublicationError("Publication counts do not reconcile")
    if (publication["silver_status"] == "PARTIAL") != bool(
        publication["rejected_count"]
    ):
        raise SilverPublicationError("Publication status and counts disagree")
    if {path.name for path in directory.iterdir()} != {*_FILES, "publication.json"}:
        raise SilverPublicationError("Unexpected files in version directory")
    records = publication.get("files")
    if not isinstance(records, list) or len(records) != 3:
        raise SilverPublicationError(
            "Publication must describe all three Parquet files"
        )
    with duckdb.connect(":memory:") as connection:
        for record, (name, (schema, count_key)) in zip(
            records, _FILES.items(), strict=True
        ):
            if not isinstance(record, dict):
                raise SilverPublicationError("Invalid publication file record")
            path = directory / name
            _no_symlinks(path, root)
            if not path.is_file() or record.get("filename") != name:
                raise SilverPublicationError(
                    f"Missing or invalid declared file: {name}"
                )
            if record.get("size_bytes") != path.stat().st_size or record.get(
                "sha256"
            ) != _sha(path):
                raise SilverPublicationError(
                    f"Version file checksum or size mismatch: {name}"
                )
            if (
                record.get("schema") != [list(column) for column in schema]
                or record.get("row_count") != publication[count_key]
            ):
                raise SilverPublicationError(
                    f"Invalid file schema or count metadata: {name}"
                )
            parquet._verify_schema(connection, path, schema)
            parquet._verify_count(connection, path, publication[count_key], name)
        parquet._verify_dimension_links(
            connection,
            directory / "facts.parquet",
            directory / "fact_dimensions.parquet",
        )
        counts = connection.execute(
            "SELECT count(*), count(DISTINCT source_occurrence_id), "
            "count(*) FILTER (WHERE NOT is_nil AND value_decimal IS NOT NULL) "
            "FROM read_parquet(?, hive_partitioning=false)",
            [str(directory / "facts.parquet")],
        ).fetchall()[0]
        if counts[0] != counts[1] or not counts[2]:
            raise SilverPublicationError(
                "Version needs unique fact IDs and a non-nil fact"
            )
    if extraction is not None:
        parquet._verify_files(extraction, *(directory / name for name in _FILES))
    return publication


def _read_active(
    filing: Path, extraction: SilverExtraction, *, root: Path
) -> str | None:
    path = filing / "active.json"
    _no_symlinks(path, root)
    if not path.exists():
        return None
    pointer = _read_json(path, root=root)
    relative = pointer.get("version_path")
    if not isinstance(relative, str):
        raise SilverPublicationError("Active pointer is missing its version path")
    version = _contained(filing, relative, root=root)
    publication_path = _contained(filing, pointer.get("publication_path"), root=root)
    if publication_path != version / "publication.json":
        raise SilverPublicationError("Active publication path does not match version")
    publication = _verify_version(version, relative, root=root)
    if pointer.get("publication_sha256") != _sha(publication_path):
        raise SilverPublicationError("Active publication checksum mismatch")
    for key in _identity(extraction):
        if pointer.get(key) != publication.get(key):
            raise SilverPublicationError(f"Active pointer {key} mismatch")
    if (
        pointer.get("cik") != extraction.reference.cik
        or pointer.get("accession_number") != extraction.reference.accession_number
    ):
        raise SilverPublicationError("Active pointer filing identity mismatch")
    _safe_id(pointer.get("processing_run_id"))
    _utc_timestamp(pointer.get("activated_at"))
    return relative


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _utc_timestamp(value: object) -> None:
    if not isinstance(value, str):
        raise SilverPublicationError("Missing UTC timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != datetime.now(UTC).utcoffset():
        raise SilverPublicationError("Timestamp must be timezone-aware UTC")


def _write_json(
    path: Path, value: dict[str, Any], *, root: Path, replace: bool = False
) -> None:
    _no_symlinks(path, root)
    if not replace and path.exists():
        raise SilverPublicationError(f"JSON record already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".publication-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _no_symlinks(path, root)
        if replace:
            os.replace(temporary, path)
        else:
            if path.exists():
                raise SilverPublicationError(f"JSON record already exists: {path}")
            os.rename(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
