"""These are the in-memory rows passed from XBRL extraction to Silver storage.

A fact represents an occurrence in the source, not a deduplicated value.
Dimensions link to that occurrence; rejected items keep their raw details
and reason so an unusable fact does not disappear without explanation.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from sec_edgar_lakehouse.filing_reference import FilingReference

SilverStatus = Literal["COMPLETE", "PARTIAL"]
PeriodKind = Literal["INSTANT", "DURATION"]
DimensionKind = Literal["EXPLICIT", "TYPED"]
ContextLocation = Literal["SEGMENT", "SCENARIO"]


@dataclass(frozen=True, slots=True)
class SilverFact:
    """One numeric item from XBRL, with its original text and exact Decimal value.

    Two equal values are still two source occurrences. Context and unit IDs
    only make sense inside their document. The occurrence ID links this item
    to its dimensions and the Gold rows that use it.
    """

    source_occurrence_id: str
    source_ordinal: int
    cik: str
    accession_number: str
    source_document_name: str
    source_sha256: str
    concept_namespace: str
    concept_local_name: str
    raw_value: str
    value_decimal: Decimal | None
    is_nil: bool
    decimals: str | None
    precision: str | None
    context_ref: str
    context_xml: str
    entity_scheme: str
    entity_identifier: str
    period_kind: PeriodKind
    period_start: date | None
    period_end: date | None
    period_instant: date | None
    unit_ref: str
    unit_expression: str
    unit_xml: str


@dataclass(frozen=True, slots=True)
class SilverDimension:
    """One explicit or typed XBRL dimension attached to an accepted fact."""

    source_occurrence_id: str
    axis_namespace: str
    axis_name: str
    member_kind: DimensionKind
    member_value: str
    typed_member_xml: str | None
    context_location: ContextLocation


@dataclass(frozen=True, slots=True)
class RejectedOccurrence:
    """A numeric item we could not accept, with its source ID and rejection reason."""

    source_occurrence_id: str
    source_ordinal: int
    concept_namespace: str | None
    concept_local_name: str | None
    raw_value: str
    context_ref: str | None
    unit_ref: str | None
    decimals: str | None
    precision: str | None
    raw_attributes: tuple[tuple[str, str], ...]
    reason_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class SilverExtraction:
    """The facts and rejected items from one verified document, before writing files."""

    reference: FilingReference
    selected_document_name: str
    selected_document_sha256: str
    status: SilverStatus
    accepted_facts: tuple[SilverFact, ...]
    dimensions: tuple[SilverDimension, ...]
    rejected_occurrences: tuple[RejectedOccurrence, ...]
    candidate_count: int
    accepted_count: int
    rejected_count: int
