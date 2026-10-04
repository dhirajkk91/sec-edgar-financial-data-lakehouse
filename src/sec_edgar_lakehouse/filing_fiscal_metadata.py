"""Extract verified fiscal DEI evidence in memory without changing the catalog."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal, cast

import duckdb

from sec_edgar_lakehouse.filing_reference import FilingReference, _normalize_cik
from sec_edgar_lakehouse.silver_extraction import (
    SilverExtractionError,
    SilverInputError,
    _build_lookup,
    _CandidateProblem,
    _expanded_name,
    _parse_context,
    _parse_date,
    _parse_xml,
    _source_occurrence_id,
    _VerifiedInput,
    _verify_bronze_input,
)

FiscalPeriod = Literal["FY", "Q1", "Q2", "Q3"]
_FIELDS = {
    "DocumentFiscalYearFocus": "fiscal_year_focus",
    "DocumentFiscalPeriodFocus": "fiscal_period_focus",
    "DocumentPeriodEndDate": "document_period_end_date",
}
_DEI = re.compile(r"http://xbrl\.sec\.gov/dei/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?")
_XBRLI = "http://www.xbrl.org/2003/instance"
_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


class FiscalMetadataInputError(Exception):
    """Bronze or catalog evidence is unavailable, unsafe or inconsistent."""


class FiscalMetadataExtractionError(Exception):
    """Shared XML structure prevents a reliable fiscal extraction."""


@dataclass(frozen=True, slots=True)
class FiscalMetadataOccurrence:
    source_occurrence_id: str
    source_ordinal: int
    concept_namespace: str
    concept_local_name: str
    raw_value: str
    normalized_value: str | None
    context_ref: str | None
    context_xml: str | None
    entity_scheme: str | None
    entity_identifier: str | None
    period_start: date | None
    period_end: date | None


@dataclass(frozen=True, slots=True)
class FiscalMetadataIssue:
    field_name: str
    reason_code: str
    message: str
    source_occurrence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FilingFiscalMetadata:
    reference: FilingReference
    selected_document_name: str
    selected_document_sha256: str
    form: str
    report_date: date
    metadata_run_id: str
    submissions_sha256: str
    extraction_version: str
    status: Literal["COMPLETE", "PARTIAL"]
    fiscal_year_focus: int | None
    fiscal_period_focus: FiscalPeriod | None
    document_period_end_date: date | None
    occurrences: tuple[FiscalMetadataOccurrence, ...]
    issues: tuple[FiscalMetadataIssue, ...]


def _catalog(
    verified: _VerifiedInput, database_path: Path
) -> tuple[str, date, str, str]:
    if not isinstance(database_path, Path):
        raise FiscalMetadataInputError("Database must be a pathlib.Path")
    try:
        database = database_path.resolve(strict=True)
        if not database.is_file():
            raise FiscalMetadataInputError("Database must be an existing file")
        identity = [verified.reference.cik, verified.reference.accession_number]
        with duckdb.connect(str(database), read_only=True) as connection:
            active = connection.execute(
                "SELECT source_document_name, source_sha256 FROM silver.active_filings "
                "WHERE cik=? AND accession_number=?",
                identity,
            ).fetchall()
            metadata = connection.execute(
                "SELECT form, report_date, metadata_run_id, submissions_sha256 "
                "FROM silver.filing_metadata WHERE cik=? AND accession_number=?",
                identity,
            ).fetchall()
    except (OSError, duckdb.Error) as exc:
        raise FiscalMetadataInputError(
            f"Cannot read existing fiscal metadata catalog: {exc}"
        ) from exc
    if len(active) != 1 or len(metadata) != 1:
        raise FiscalMetadataInputError(
            "Require exactly one active filing and one filing metadata row"
        )
    if active[0] != (verified.document_name, verified.document_sha256):
        raise FiscalMetadataInputError(
            "Active document name/checksum does not match verified Bronze"
        )
    form, report, run_id, checksum = metadata[0]
    if form not in ("10-K", "10-K/A", "10-Q", "10-Q/A") or type(report) is not date:
        raise FiscalMetadataInputError(
            "Catalog requires a supported form and a non-null DATE report date"
        )
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(checksum, str)
        or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
    ):
        raise FiscalMetadataInputError(
            "Catalog requires metadata run ID and submissions SHA-256"
        )
    return form, report, run_id, checksum


def _recognized(element: ET.Element) -> bool:
    namespace, local = _expanded_name(element.tag)
    return (
        namespace is not None
        and _DEI.fullmatch(namespace) is not None
        and local in _FIELDS
    )


def _evidence(
    element: ET.Element,
    context: ET.Element | None,
    verified: _VerifiedInput,
    ordinal: int,
) -> FiscalMetadataOccurrence:
    namespace, local = _expanded_name(element.tag)
    scheme = identifier = None
    start = end = None
    if context is not None:
        identifiers = context.findall(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}identifier")
        if len(identifiers) == 1:
            scheme, identifier = identifiers[0].get("scheme"), identifiers[0].text
        # Preserve independently readable evidence even when the full context is unusable.
        for tag in ("startDate", "endDate"):
            dates = context.findall(f"{{{_XBRLI}}}period/{{{_XBRLI}}}{tag}")
            if len(dates) == 1:
                try:
                    parsed = _parse_date(dates[0].text, element.get("contextRef") or "")
                except _CandidateProblem:
                    continue
                if tag == "startDate":
                    start = parsed
                else:
                    end = parsed
    return FiscalMetadataOccurrence(
        _source_occurrence_id(verified, ordinal),
        ordinal,
        cast(str, namespace),
        cast(str, local),
        "".join(element.itertext()),
        None,
        element.get("contextRef"),
        ET.tostring(context, encoding="unicode") if context is not None else None,
        scheme,
        identifier,
        start,
        end,
    )


def _normalize(
    element: ET.Element,
    occurrence: FiscalMetadataOccurrence,
    context: ET.Element | None,
    scopes: dict[int, dict[str, str]],
    verified: _VerifiedInput,
    form: str,
    report: date,
) -> str:
    if len(element) or element.get(_NIL) not in (None, "false", "0"):
        raise _CandidateProblem(
            "INVALID_FISCAL_FIELD", "Fiscal fact must be a non-nil leaf element"
        )
    if context is None:
        raise _CandidateProblem(
            "INVALID_FISCAL_FIELD", "Fiscal fact has no resolvable contextRef"
        )
    try:
        parsed = _parse_context(context, scopes)
    except _CandidateProblem as exc:
        raise _CandidateProblem("INVALID_FISCAL_FIELD", exc.reason) from exc
    if parsed.period_kind != "DURATION":
        raise _CandidateProblem(
            "INVALID_FISCAL_FIELD", "Fiscal context must be a valid duration"
        )
    if context.findall(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}segment") or context.findall(
        f"{{{_XBRLI}}}scenario"
    ):
        raise _CandidateProblem(
            "INVALID_FISCAL_FIELD",
            "Fiscal context must have no segment or scenario content",
        )
    try:
        entity_cik = _normalize_cik(occurrence.entity_identifier or "")
    except ValueError as exc:
        raise _CandidateProblem(
            "INVALID_FISCAL_FIELD", "Fiscal context has an invalid entity CIK"
        ) from exc
    if (
        occurrence.entity_scheme
        not in ("http://www.sec.gov/CIK", "https://www.sec.gov/CIK")
        or entity_cik != verified.reference.cik
    ):
        raise _CandidateProblem(
            "INVALID_FISCAL_FIELD",
            "Fiscal context entity does not match the filing CIK",
        )
    if parsed.period_end != report:
        raise _CandidateProblem(
            "REPORT_DATE_MISMATCH",
            "Fiscal context end does not match the verified report date",
        )
    text = occurrence.raw_value.strip()
    if occurrence.concept_local_name == "DocumentFiscalYearFocus":
        if re.fullmatch(r"[0-9]{4}", text) is None or int(text) == 0:
            raise _CandidateProblem(
                "INVALID_FISCAL_FIELD",
                "Fiscal year must be four ASCII digits and nonzero",
            )
        return text
    if occurrence.concept_local_name == "DocumentFiscalPeriodFocus":
        if text not in ("FY", "Q1", "Q2", "Q3"):
            raise _CandidateProblem(
                "INVALID_FISCAL_FIELD", "Fiscal focus must be FY, Q1, Q2 or Q3"
            )
        if (form in ("10-K", "10-K/A")) != (text == "FY"):
            raise _CandidateProblem(
                "FISCAL_PERIOD_FORM_MISMATCH",
                "Fiscal focus is inconsistent with the verified filing form",
            )
        return text
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text) is None:
        raise _CandidateProblem(
            "INVALID_FISCAL_FIELD", "Document end date must be valid YYYY-MM-DD"
        )
    try:
        value = date.fromisoformat(text)
    except ValueError as exc:
        raise _CandidateProblem(
            "INVALID_FISCAL_FIELD", "Document end date is not a valid date"
        ) from exc
    if value != report:
        raise _CandidateProblem(
            "REPORT_DATE_MISMATCH",
            "Document end date does not match the verified report date",
        )
    return value.isoformat()


def extract_filing_fiscal_metadata(
    filing_directory: Path, manifest_path: Path, *, database_path: Path
) -> FilingFiscalMetadata:
    """Verify Bronze and active catalog lineage, then resolve three DEI fields in memory."""
    try:
        verified = _verify_bronze_input(filing_directory, manifest_path)
    except SilverInputError as exc:
        raise FiscalMetadataInputError(f"Invalid Bronze fiscal input: {exc}") from exc
    form, report, run_id, checksum = _catalog(verified, database_path)
    try:
        root, scopes = _parse_xml(verified.document_content)
        if root.tag != f"{{{_XBRLI}}}xbrl":
            raise FiscalMetadataExtractionError(
                "Selected document must have an XBRL instance root"
            )
        contexts = _build_lookup(root, f"{{{_XBRLI}}}context", "context")
        for child in root:
            if any(_recognized(descendant) for descendant in list(child.iter())[1:]):
                raise FiscalMetadataExtractionError(
                    "Recognized fiscal facts must be directly beneath the XBRL root"
                )
    except SilverExtractionError as exc:
        raise FiscalMetadataExtractionError(f"Invalid fiscal XML: {exc}") from exc
    occurrences: list[FiscalMetadataOccurrence] = []
    issues: list[FiscalMetadataIssue] = []
    for ordinal, element in enumerate(root, start=1):
        if not _recognized(element):
            continue
        context = contexts.get(element.get("contextRef") or "")
        occurrence = _evidence(element, context, verified, ordinal)
        try:
            normalized = _normalize(
                element, occurrence, context, scopes, verified, form, report
            )
            occurrence = replace(occurrence, normalized_value=normalized)
        except _CandidateProblem as exc:
            issues.append(
                FiscalMetadataIssue(
                    _FIELDS[occurrence.concept_local_name],
                    exc.code,
                    exc.reason,
                    (occurrence.source_occurrence_id,),
                )
            )
        occurrences.append(occurrence)
    resolved: dict[str, str | None] = {}
    for concept, field in _FIELDS.items():
        matching = [o for o in occurrences if o.concept_local_name == concept]
        eligible = [o for o in matching if o.normalized_value is not None]
        values = {o.normalized_value for o in eligible}
        resolved[field] = next(iter(values)) if len(values) == 1 else None
        if not matching:
            issues.append(
                FiscalMetadataIssue(
                    field,
                    "MISSING_FISCAL_FIELD",
                    f"No recognized {concept} occurrence",
                    (),
                )
            )
        elif len(values) > 1:
            issues.append(
                FiscalMetadataIssue(
                    field,
                    "CONFLICTING_FISCAL_FIELD",
                    f"Eligible {concept} occurrences disagree",
                    tuple(o.source_occurrence_id for o in eligible),
                )
            )
    year, focus, document_end = (resolved[f] for f in _FIELDS.values())
    return FilingFiscalMetadata(
        verified.reference,
        verified.document_name,
        verified.document_sha256,
        form,
        report,
        run_id,
        checksum,
        "1",
        "COMPLETE" if all(resolved.values()) and not issues else "PARTIAL",
        int(year) if year is not None else None,
        cast(FiscalPeriod | None, focus),
        date.fromisoformat(document_end) if document_end is not None else None,
        tuple(occurrences),
        tuple(issues),
    )
