"""Verify one published Bronze filing and extract numeric XBRL facts."""

import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import unquote

from sec_edgar_lakehouse.filing_reference import FilingReference
from sec_edgar_lakehouse.silver_models import (
    ContextLocation,
    DimensionKind,
    PeriodKind,
    RejectedOccurrence,
    SilverDimension,
    SilverExtraction,
    SilverFact,
    SilverStatus,
)

_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"
_XBRL_ROOT = f"{{{_XBRLI}}}xbrl"
_CONTEXT_TAG = f"{{{_XBRLI}}}context"
_UNIT_TAG = f"{{{_XBRLI}}}unit"
_EXPLICIT_MEMBER_TAG = f"{{{_XBRLDI}}}explicitMember"
_TYPED_MEMBER_TAG = f"{{{_XBRLDI}}}typedMember"
_NIL_ATTRIBUTE = f"{{{_XSI}}}nil"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_NUMERIC_PATTERN = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)
_NCNAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9._-]*\Z")


class SilverInputError(Exception):
    """Published Bronze input is unsafe, inconsistent, or unverifiable."""


class SilverExtractionError(Exception):
    """The verified XBRL document cannot produce a reliable result."""


@dataclass(frozen=True, slots=True)
class _VerifiedInput:
    reference: FilingReference
    document_name: str
    document_sha256: str
    document_content: bytes


@dataclass(frozen=True, slots=True)
class _Dimension:
    axis_namespace: str
    axis_name: str
    member_kind: DimensionKind
    member_value: str
    typed_member_xml: str | None
    context_location: ContextLocation


@dataclass(frozen=True, slots=True)
class _Context:
    context_ref: str
    context_xml: str
    entity_scheme: str
    entity_identifier: str
    period_kind: PeriodKind
    period_start: date | None
    period_end: date | None
    period_instant: date | None
    dimensions: tuple[_Dimension, ...]


@dataclass(frozen=True, slots=True)
class _Unit:
    unit_ref: str
    expression: str
    unit_xml: str


class _CandidateProblem(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def extract_filing_facts(
    filing_directory: Path,
    manifest_path: Path,
) -> SilverExtraction:
    """Verify a published Bronze filing and return its numeric facts in memory."""
    verified = _verify_bronze_input(filing_directory, manifest_path)
    try:
        return _extract_verified_document(verified)
    except SilverExtractionError:
        raise
    except Exception as exc:
        # Never make a partly accumulated prefix look like a valid extraction.
        raise SilverExtractionError(
            f"Unexpected XBRL extraction failure: {exc}"
        ) from exc


def _verify_bronze_input(filing_directory: Path, manifest_path: Path) -> _VerifiedInput:
    if not isinstance(filing_directory, Path):
        raise SilverInputError("Filing directory must be a pathlib.Path")
    if not isinstance(manifest_path, Path):
        raise SilverInputError("Manifest path must be a pathlib.Path")

    filing_directory = _require_directory(filing_directory, "filing directory")
    manifests_directory = _require_directory(
        filing_directory / "manifests", "manifest directory"
    )
    manifest_path = _require_regular_file(
        manifest_path, manifests_directory, "manifest"
    )
    if manifest_path.parent != manifests_directory:
        raise SilverInputError("Manifest must be directly inside the manifests folder")

    manifest = _read_json(manifest_path, "manifest")
    status = manifest.get("status")
    if status not in {"COMPLETE", "PARTIAL"}:
        raise SilverInputError(
            f"Bronze manifest status must be COMPLETE or PARTIAL, not {status!r}"
        )
    if manifest.get("source_complete") is not True:
        raise SilverInputError("Bronze manifest must have source_complete=true")
    if manifest.get("parser_ready") is not True:
        raise SilverInputError("Bronze manifest must have parser_ready=true")

    reference = _manifest_reference(manifest)
    expected_directory_name = f"accession={reference.accession_number}"
    expected_parent_name = f"cik={reference.cik}"
    if (
        filing_directory.name != expected_directory_name
        or filing_directory.parent.name != expected_parent_name
    ):
        raise SilverInputError(
            "Filing directory is not the canonical path for the manifest identity"
        )

    manifest_files = _record_list(manifest.get("files"), "manifest files")
    discovery_record = _one_record(
        manifest_files,
        section="metadata",
        document_name="discovery.json",
        subject="verified discovery metadata",
    )
    if discovery_record.get("status") != "VERIFIED":
        raise SilverInputError("Manifest discovery.json entry must be VERIFIED")
    discovery_path = _require_regular_file(
        filing_directory / "metadata" / "discovery.json",
        filing_directory,
        "discovery.json",
    )
    discovery_content = _read_verified_bytes(
        discovery_path, discovery_record, "discovery.json"
    )
    discovery = _parse_json_bytes(discovery_content, "discovery.json")
    _require_identity(discovery, reference, "discovery.json")

    inventory = _record_list(discovery.get("inventory"), "discovery inventory")
    candidates = [
        record
        for record in inventory
        if record.get("section") == "data-file"
        and isinstance(record.get("description"), str)
        and cast(str, record["description"]).strip().casefold()
        == "extracted xbrl instance document"
        and isinstance(record.get("document_name"), str)
        and cast(str, record["document_name"]).lower().endswith(".xml")
    ]
    if len(candidates) != 1:
        raise SilverInputError(
            "Discovery must contain exactly one EXTRACTED XBRL INSTANCE DOCUMENT XML "
            f"entry; found {len(candidates)}"
        )
    document_name = cast(str, candidates[0]["document_name"])
    _validate_filename(document_name)

    document_record = _one_record(
        manifest_files,
        section="data-file",
        document_name=document_name,
        subject="selected XBRL document",
    )
    if document_record.get("status") != "VERIFIED":
        raise SilverInputError(
            f"Selected XBRL document {document_name!r} must be VERIFIED"
        )
    document_path = _require_regular_file(
        filing_directory / "sec-derived" / document_name,
        filing_directory,
        "selected XBRL document",
    )
    document_content = _read_verified_bytes(
        document_path, document_record, "selected XBRL document"
    )
    document_sha256 = cast(str, document_record["sha256"])
    return _VerifiedInput(reference, document_name, document_sha256, document_content)


def _require_directory(path: Path, subject: str) -> Path:
    if path.is_symlink():
        raise SilverInputError(f"{subject.capitalize()} must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SilverInputError(
            f"{subject.capitalize()} does not exist: {path}"
        ) from exc
    if not resolved.is_dir():
        raise SilverInputError(f"{subject.capitalize()} is not a directory: {path}")
    return resolved


def _require_regular_file(path: Path, root: Path, subject: str) -> Path:
    if ".." in path.parts:
        raise SilverInputError(
            f"{subject.capitalize()} path contains a traversal component: {path}"
        )
    current = path
    while True:
        if current.is_symlink():
            raise SilverInputError(
                f"{subject.capitalize()} path must not contain symlinks: {current}"
            )
        try:
            if current.resolve(strict=True) == root:
                break
        except OSError as exc:
            raise SilverInputError(
                f"{subject.capitalize()} does not exist: {path}"
            ) from exc
        if current.parent == current:
            raise SilverInputError(f"{subject.capitalize()} is outside {root}")
        current = current.parent
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SilverInputError(f"{subject.capitalize()} is outside {root}") from exc
    if not resolved.is_file():
        raise SilverInputError(f"{subject.capitalize()} is not a regular file: {path}")
    return resolved


def _read_json(path: Path, subject: str) -> dict[str, Any]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SilverInputError(f"Could not read {subject}: {path}: {exc}") from exc
    return _parse_json_bytes(content, subject)


def _parse_json_bytes(content: bytes, subject: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SilverInputError(
            f"{subject.capitalize()} is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise SilverInputError(f"{subject.capitalize()} must contain a JSON object")
    return cast(dict[str, Any], value)


def _manifest_reference(manifest: dict[str, Any]) -> FilingReference:
    cik = manifest.get("cik")
    accession_number = manifest.get("accession_number")
    if not isinstance(cik, str) or not isinstance(accession_number, str):
        raise SilverInputError("Manifest has invalid filing identity")
    try:
        return FilingReference(cik, accession_number)
    except (TypeError, ValueError) as exc:
        raise SilverInputError(f"Manifest has invalid filing identity: {exc}") from exc


def _require_identity(
    record: dict[str, Any], reference: FilingReference, subject: str
) -> None:
    if (
        record.get("cik") != reference.cik
        or record.get("accession_number") != reference.accession_number
    ):
        raise SilverInputError(
            f"{subject.capitalize()} filing identity does not match the manifest"
        )


def _record_list(value: object, subject: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SilverInputError(f"{subject.capitalize()} must be a list of objects")
    return cast(list[dict[str, Any]], value)


def _one_record(
    records: list[dict[str, Any]],
    *,
    section: str,
    document_name: str,
    subject: str,
) -> dict[str, Any]:
    matches = [
        record
        for record in records
        if record.get("section") == section
        and record.get("document_name") == document_name
    ]
    if len(matches) != 1:
        raise SilverInputError(
            f"Manifest must contain exactly one {subject} entry; found {len(matches)}"
        )
    return matches[0]


def _read_verified_bytes(path: Path, evidence: dict[str, Any], subject: str) -> bytes:
    size = evidence.get("size_bytes")
    checksum = evidence.get("sha256")
    if type(size) is not int or size < 0:
        raise SilverInputError(f"Manifest has invalid size for {subject}")
    if not isinstance(checksum, str) or _SHA256_PATTERN.fullmatch(checksum) is None:
        raise SilverInputError(f"Manifest has invalid SHA-256 for {subject}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SilverInputError(f"Could not read {subject}: {path}: {exc}") from exc
    if len(content) != size:
        raise SilverInputError(
            f"{subject.capitalize()} size does not match the manifest"
        )
    if hashlib.sha256(content).hexdigest() != checksum:
        raise SilverInputError(
            f"{subject.capitalize()} SHA-256 does not match the manifest"
        )
    return content


def _validate_filename(name: str) -> None:
    decoded = unquote(name)
    if (
        not name
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
        or Path(name).name != name
    ):
        raise SilverInputError(f"Unsafe selected XBRL filename: {name!r}")


def _extract_verified_document(verified: _VerifiedInput) -> SilverExtraction:
    root, namespace_scopes = _parse_xml(verified.document_content)
    if root.tag != _XBRL_ROOT:
        raise SilverExtractionError("Selected XML root must be an XBRL instance")
    _reject_nested_candidates(root)

    contexts = _build_lookup(root, _CONTEXT_TAG, "context")
    units = _build_lookup(root, _UNIT_TAG, "unit")
    accepted: list[SilverFact] = []
    dimensions: list[SilverDimension] = []
    rejected: list[RejectedOccurrence] = []
    candidate_count = 0
    non_nil_count = 0

    for ordinal, element in enumerate(root, start=1):
        if "unitRef" not in element.attrib:
            continue
        candidate_count += 1
        source_id = _source_occurrence_id(verified, ordinal)
        namespace, local_name = _expanded_name(element.tag)
        raw_value = "".join(element.itertext())
        attributes = tuple(sorted(element.attrib.items()))
        try:
            fact, parsed_dimensions = _parse_candidate(
                element,
                ordinal,
                source_id,
                namespace,
                local_name,
                raw_value,
                contexts,
                units,
                namespace_scopes,
                verified,
            )
        except _CandidateProblem as problem:
            rejected.append(
                RejectedOccurrence(
                    source_occurrence_id=source_id,
                    source_ordinal=ordinal,
                    concept_namespace=namespace,
                    concept_local_name=local_name,
                    raw_value=raw_value,
                    context_ref=element.get("contextRef"),
                    unit_ref=element.get("unitRef"),
                    decimals=element.get("decimals"),
                    precision=element.get("precision"),
                    raw_attributes=attributes,
                    reason_code=problem.code,
                    reason=problem.reason,
                )
            )
            continue
        accepted.append(fact)
        dimensions.extend(parsed_dimensions)
        if not fact.is_nil:
            non_nil_count += 1

    if candidate_count == 0:
        raise SilverExtractionError("XBRL instance contains no numeric candidates")
    if non_nil_count == 0:
        raise SilverExtractionError("No non-nil numeric fact was accepted")
    if candidate_count != len(accepted) + len(rejected):
        raise SilverExtractionError("Candidate counts do not reconcile")

    status: SilverStatus = "COMPLETE" if not rejected else "PARTIAL"
    return SilverExtraction(
        reference=verified.reference,
        selected_document_name=verified.document_name,
        selected_document_sha256=verified.document_sha256,
        status=status,
        accepted_facts=tuple(accepted),
        dimensions=tuple(dimensions),
        rejected_occurrences=tuple(rejected),
        candidate_count=candidate_count,
        accepted_count=len(accepted),
        rejected_count=len(rejected),
    )


def _parse_xml(content: bytes) -> tuple[ET.Element, dict[int, dict[str, str]]]:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise SilverExtractionError("XBRL instance must not contain a DTD or entities")
    namespace_scopes: dict[int, dict[str, str]] = {}
    pending_namespaces: list[tuple[str, str]] = []
    scope_stack: list[dict[str, str]] = []
    root: ET.Element | None = None
    try:
        iterator = ET.iterparse(
            io.BytesIO(content), events=("start-ns", "start", "end")
        )
        for event, value in iterator:
            if event == "start-ns":
                prefix, namespace = cast(tuple[str, str], value)
                pending_namespaces.append((prefix or "", namespace))
            elif event == "start":
                element = cast(ET.Element, value)
                if root is None:
                    root = element
                scope = dict(scope_stack[-1]) if scope_stack else {}
                scope.update(pending_namespaces)
                pending_namespaces.clear()
                namespace_scopes[id(element)] = scope
                scope_stack.append(scope)
            else:
                scope_stack.pop()
    except (ET.ParseError, UnicodeError) as exc:
        raise SilverExtractionError(f"Selected XML is not well-formed: {exc}") from exc
    if root is None:
        raise SilverExtractionError("Selected XML contains no root element")
    return root, namespace_scopes


def _reject_nested_candidates(root: ET.Element) -> None:
    for child in root:
        for descendant in list(child.iter())[1:]:
            if "unitRef" in descendant.attrib:
                raise SilverExtractionError(
                    "XBRL instance contains a numeric item below the root level"
                )


def _build_lookup(root: ET.Element, tag: str, subject: str) -> dict[str, ET.Element]:
    lookup: dict[str, ET.Element] = {}
    for element in root.findall(tag):
        identifier = element.get("id")
        if not identifier:
            continue
        if identifier in lookup:
            raise SilverExtractionError(
                f"XBRL instance contains duplicate {subject} id {identifier!r}"
            )
        lookup[identifier] = element
    return lookup


def _parse_candidate(
    element: ET.Element,
    ordinal: int,
    source_id: str,
    concept_namespace: str | None,
    concept_local_name: str | None,
    raw_value: str,
    contexts: dict[str, ET.Element],
    units: dict[str, ET.Element],
    namespace_scopes: dict[int, dict[str, str]],
    verified: _VerifiedInput,
) -> tuple[SilverFact, tuple[SilverDimension, ...]]:
    if not concept_namespace or not concept_local_name:
        _problem("INVALID_CONCEPT", "Numeric concept must have an expanded namespace")
    if list(element):
        _problem("INVALID_VALUE", "Numeric fact must contain text, not child elements")

    context_ref = element.get("contextRef")
    if not context_ref:
        _problem("MISSING_CONTEXT_REF", "Numeric fact is missing contextRef")
    context_element = contexts.get(context_ref)
    if context_element is None:
        _problem("CONTEXT_NOT_FOUND", f"Context {context_ref!r} was not found")
    context = _parse_context(context_element, namespace_scopes)

    unit_ref = element.get("unitRef")
    if not unit_ref:
        _problem("MISSING_UNIT_REF", "Numeric fact is missing unitRef")
    unit_element = units.get(unit_ref)
    if unit_element is None:
        _problem("UNIT_NOT_FOUND", f"Unit {unit_ref!r} was not found")
    unit = _parse_unit(unit_element, namespace_scopes)

    nil_value = element.get(_NIL_ATTRIBUTE)
    if nil_value is None or nil_value in {"false", "0"}:
        is_nil = False
        value_decimal = _parse_decimal(raw_value)
    elif nil_value in {"true", "1"}:
        if raw_value.strip():
            _problem("INVALID_NIL", "A nil numeric fact must not contain a value")
        is_nil = True
        value_decimal = None
    else:
        _problem("INVALID_NIL", f"Invalid xsi:nil value {nil_value!r}")

    fact = SilverFact(
        source_occurrence_id=source_id,
        source_ordinal=ordinal,
        cik=verified.reference.cik,
        accession_number=verified.reference.accession_number,
        source_document_name=verified.document_name,
        source_sha256=verified.document_sha256,
        concept_namespace=concept_namespace,
        concept_local_name=concept_local_name,
        raw_value=raw_value,
        value_decimal=value_decimal,
        is_nil=is_nil,
        decimals=element.get("decimals"),
        precision=element.get("precision"),
        context_ref=context_ref,
        context_xml=context.context_xml,
        entity_scheme=context.entity_scheme,
        entity_identifier=context.entity_identifier,
        period_kind=context.period_kind,
        period_start=context.period_start,
        period_end=context.period_end,
        period_instant=context.period_instant,
        unit_ref=unit_ref,
        unit_expression=unit.expression,
        unit_xml=unit.unit_xml,
    )
    dimensions = tuple(
        SilverDimension(
            source_occurrence_id=source_id,
            axis_namespace=item.axis_namespace,
            axis_name=item.axis_name,
            member_kind=item.member_kind,
            member_value=item.member_value,
            typed_member_xml=item.typed_member_xml,
            context_location=item.context_location,
        )
        for item in context.dimensions
    )
    return fact, dimensions


def _parse_context(
    context: ET.Element, namespace_scopes: dict[int, dict[str, str]]
) -> _Context:
    context_ref = context.get("id") or ""
    entities = context.findall(f"{{{_XBRLI}}}entity")
    periods = context.findall(f"{{{_XBRLI}}}period")
    if len(entities) != 1 or len(periods) != 1:
        _problem(
            "INVALID_CONTEXT",
            f"Context {context_ref!r} must contain one entity and one period",
        )
    entity = entities[0]
    segments = entity.findall(f"{{{_XBRLI}}}segment")
    scenarios = context.findall(f"{{{_XBRLI}}}scenario")
    if any(
        child.tag not in {f"{{{_XBRLI}}}identifier", f"{{{_XBRLI}}}segment"}
        for child in entity
    ) or any(
        child.tag
        not in {
            f"{{{_XBRLI}}}entity",
            f"{{{_XBRLI}}}period",
            f"{{{_XBRLI}}}scenario",
        }
        for child in context
    ):
        _problem(
            "INVALID_CONTEXT",
            f"Context {context_ref!r} contains unsupported structure",
        )
    if len(segments) > 1 or len(scenarios) > 1:
        _problem(
            "INVALID_DIMENSION",
            f"Context {context_ref!r} repeats segment or scenario content",
        )
    identifiers = entity.findall(f"{{{_XBRLI}}}identifier")
    if len(identifiers) != 1:
        _problem(
            "INVALID_CONTEXT",
            f"Context {context_ref!r} must contain one entity identifier",
        )
    identifier = identifiers[0]
    entity_identifier = (identifier.text or "").strip()
    entity_scheme = (identifier.get("scheme") or "").strip()
    if not entity_identifier or not entity_scheme:
        _problem(
            "INVALID_CONTEXT",
            f"Context {context_ref!r} has an incomplete entity identifier",
        )

    period_kind, start, end, instant = _parse_period(periods[0], context_ref)
    parsed_dimensions: list[_Dimension] = []
    for segment in segments:
        parsed_dimensions.extend(
            _parse_dimensions(segment, "SEGMENT", namespace_scopes, context_ref)
        )
    for scenario in scenarios:
        parsed_dimensions.extend(
            _parse_dimensions(scenario, "SCENARIO", namespace_scopes, context_ref)
        )
    dimension_keys = [
        (dimension.axis_namespace, dimension.axis_name)
        for dimension in parsed_dimensions
    ]
    if len(dimension_keys) != len(set(dimension_keys)):
        _problem(
            "INVALID_DIMENSION",
            f"Context {context_ref!r} repeats a dimension axis",
        )
    return _Context(
        context_ref=context_ref,
        context_xml=ET.tostring(context, encoding="unicode"),
        entity_scheme=entity_scheme,
        entity_identifier=entity_identifier,
        period_kind=period_kind,
        period_start=start,
        period_end=end,
        period_instant=instant,
        dimensions=tuple(parsed_dimensions),
    )


def _parse_period(
    period: ET.Element, context_ref: str
) -> tuple[PeriodKind, date | None, date | None, date | None]:
    children = list(period)
    if len(children) == 1 and children[0].tag == f"{{{_XBRLI}}}instant":
        instant = _parse_date(children[0].text, context_ref)
        return "INSTANT", None, None, instant
    if (
        len(children) == 2
        and children[0].tag == f"{{{_XBRLI}}}startDate"
        and children[1].tag == f"{{{_XBRLI}}}endDate"
    ):
        start = _parse_date(children[0].text, context_ref)
        end = _parse_date(children[1].text, context_ref)
        if start > end:
            _problem(
                "INVALID_PERIOD",
                f"Context {context_ref!r} has a start date after its end date",
            )
        return "DURATION", start, end, None
    _problem(
        "INVALID_PERIOD",
        f"Context {context_ref!r} does not contain one instant or duration period",
    )


def _parse_date(value: str | None, context_ref: str) -> date:
    text = "" if value is None else value.strip()
    if _DATE_PATTERN.fullmatch(text) is None:
        _problem("INVALID_PERIOD", f"Context {context_ref!r} contains an invalid date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise _CandidateProblem(
            "INVALID_PERIOD", f"Context {context_ref!r} contains an invalid date"
        ) from exc


def _parse_dimensions(
    container: ET.Element,
    location: ContextLocation,
    namespace_scopes: dict[int, dict[str, str]],
    context_ref: str,
) -> list[_Dimension]:
    dimensions: list[_Dimension] = []
    if (container.text or "").strip() or any(
        (member.tail or "").strip() for member in container
    ):
        _problem(
            "INVALID_DIMENSION",
            f"Context {context_ref!r} contains unsupported {location.lower()} text",
        )
    for member in container:
        if member.tag not in {_EXPLICIT_MEMBER_TAG, _TYPED_MEMBER_TAG}:
            _problem(
                "INVALID_DIMENSION",
                f"Context {context_ref!r} contains unsupported {location.lower()} content",
            )
        axis_value = member.get("dimension")
        axis_namespace, axis_name = _resolve_qname(
            axis_value, namespace_scopes[id(member)], "dimension axis", context_ref
        )
        if member.tag == _EXPLICIT_MEMBER_TAG:
            if list(member):
                _problem(
                    "INVALID_DIMENSION",
                    f"Context {context_ref!r} has a malformed explicit dimension",
                )
            member_namespace, member_name = _resolve_qname(
                (member.text or "").strip(),
                namespace_scopes[id(member)],
                "explicit member",
                context_ref,
            )
            dimensions.append(
                _Dimension(
                    axis_namespace,
                    axis_name,
                    "EXPLICIT",
                    f"{{{member_namespace}}}{member_name}",
                    None,
                    location,
                )
            )
            continue
        children = list(member)
        if (
            len(children) != 1
            or (member.text or "").strip()
            or (children[0].tail or "").strip()
        ):
            _problem(
                "INVALID_DIMENSION",
                f"Context {context_ref!r} has a malformed typed dimension",
            )
        typed_xml = ET.tostring(children[0], encoding="unicode")
        member_value = "".join(children[0].itertext()).strip() or typed_xml
        dimensions.append(
            _Dimension(
                axis_namespace,
                axis_name,
                "TYPED",
                member_value,
                typed_xml,
                location,
            )
        )
    return dimensions


def _parse_unit(unit: ET.Element, namespace_scopes: dict[int, dict[str, str]]) -> _Unit:
    unit_ref = unit.get("id") or ""
    children = list(unit)
    if children and all(child.tag == f"{{{_XBRLI}}}measure" for child in children):
        expressions = [
            _measure_expression(child, namespace_scopes, unit_ref) for child in children
        ]
        expression = " * ".join(expressions)
    elif len(children) == 1 and children[0].tag == f"{{{_XBRLI}}}divide":
        divide = children[0]
        numerators = divide.findall(f"{{{_XBRLI}}}unitNumerator")
        denominators = divide.findall(f"{{{_XBRLI}}}unitDenominator")
        if len(numerators) != 1 or len(denominators) != 1 or len(divide) != 2:
            _problem("INVALID_UNIT", f"Unit {unit_ref!r} has a malformed divide")
        numerator = _measure_group(numerators[0], namespace_scopes, unit_ref)
        denominator = _measure_group(denominators[0], namespace_scopes, unit_ref)
        expression = f"({numerator}) / ({denominator})"
    else:
        _problem("INVALID_UNIT", f"Unit {unit_ref!r} has an unsupported structure")
    return _Unit(unit_ref, expression, ET.tostring(unit, encoding="unicode"))


def _measure_group(
    group: ET.Element,
    namespace_scopes: dict[int, dict[str, str]],
    unit_ref: str,
) -> str:
    measures = list(group)
    if not measures or any(
        measure.tag != f"{{{_XBRLI}}}measure" for measure in measures
    ):
        _problem("INVALID_UNIT", f"Unit {unit_ref!r} has an invalid measure group")
    return " * ".join(
        _measure_expression(measure, namespace_scopes, unit_ref) for measure in measures
    )


def _measure_expression(
    measure: ET.Element,
    namespace_scopes: dict[int, dict[str, str]],
    unit_ref: str,
) -> str:
    namespace, name = _resolve_qname(
        (measure.text or "").strip(),
        namespace_scopes[id(measure)],
        "unit measure",
        unit_ref,
        code="INVALID_UNIT",
    )
    return f"{{{namespace}}}{name}"


def _resolve_qname(
    value: str | None,
    namespaces: dict[str, str],
    subject: str,
    owner: str,
    *,
    code: str = "INVALID_DIMENSION",
) -> tuple[str, str]:
    text = "" if value is None else value.strip()
    if not text or text.count(":") > 1:
        _problem(code, f"{subject.capitalize()} in {owner!r} is not a valid QName")
    if ":" in text:
        prefix, local_name = text.split(":", 1)
    else:
        prefix, local_name = "", text
    namespace = namespaces.get(prefix)
    invalid_prefix = bool(prefix) and _NCNAME_PATTERN.fullmatch(prefix) is None
    if (
        invalid_prefix
        or _NCNAME_PATTERN.fullmatch(local_name) is None
        or namespace is None
    ):
        _problem(
            code,
            f"{subject.capitalize()} in {owner!r} is not a resolvable QName",
        )
    return namespace, local_name


def _parse_decimal(raw_value: str) -> Decimal:
    numeric_text = raw_value.strip()
    if not numeric_text:
        _problem("INVALID_VALUE", "Non-nil numeric fact has an empty value")
    if _NUMERIC_PATTERN.fullmatch(numeric_text) is None:
        _problem("INVALID_VALUE", f"Numeric value {raw_value!r} is not a decimal")
    try:
        value = Decimal(numeric_text)
    except InvalidOperation as exc:
        raise _CandidateProblem(
            "INVALID_VALUE", f"Numeric value {raw_value!r} is not a decimal"
        ) from exc
    if not value.is_finite():
        _problem("NON_FINITE_VALUE", f"Numeric value {raw_value!r} is not finite")
    if not _fits_decimal_38_18(value):
        _problem(
            "DECIMAL_OUT_OF_RANGE",
            f"Numeric value {raw_value!r} cannot fit DECIMAL(38,18) exactly",
        )
    return value


def _fits_decimal_38_18(value: Decimal) -> bool:
    if value.is_zero():
        return True
    _, raw_digits, raw_exponent = value.as_tuple()
    if not isinstance(raw_exponent, int):
        return False
    digits = list(raw_digits)
    exponent = raw_exponent
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    scale = max(-exponent, 0)
    integer_digits = max(len(digits) + exponent, 0)
    return scale <= 18 and integer_digits <= 20


def _expanded_name(tag: object) -> tuple[str | None, str | None]:
    if not isinstance(tag, str) or not tag.startswith("{") or "}" not in tag:
        return None, tag if isinstance(tag, str) and tag else None
    namespace, local_name = tag[1:].split("}", 1)
    if not namespace or not local_name:
        return None, None
    return namespace, local_name


def _source_occurrence_id(verified: _VerifiedInput, ordinal: int) -> str:
    parts = (
        verified.reference.cik,
        verified.reference.accession_number,
        verified.document_name,
        verified.document_sha256,
        str(ordinal),
    )
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _problem(code: str, reason: str) -> NoReturn:
    raise _CandidateProblem(code, reason)
