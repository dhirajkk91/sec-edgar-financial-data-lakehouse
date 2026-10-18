"""Small XBRL documents are wrapped in local Bronze evidence with real checksums.

This keeps extraction tests independent of the downloader while still
exercising source verification. Contexts, units, duplicate values and bad
numeric items show which problems reject one fact and which fail the document.
"""

import hashlib
import json
import socket
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sec_edgar_lakehouse import (
    RejectedOccurrence,
    SilverExtractionError,
    SilverInputError,
    extract_filing_facts,
)
from sec_edgar_lakehouse.silver.silver_inspect import main as inspect_main

CIK = "0000320193"
ACCESSION = "0000320193-24-000123"
DOCUMENT_NAME = "apple-20240928_htm.xml"
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
US_GAAP = "http://fasb.org/us-gaap/2024"
ISO4217 = "http://www.xbrl.org/2003/iso4217"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

XML_ENCODINGS = [
    pytest.param("utf-8", "UTF-8", b"", id="utf-8"),
    pytest.param("utf-8", "UTF-8", b"\xef\xbb\xbf", id="utf-8-bom"),
    pytest.param("utf-16-le", "UTF-16", b"\xff\xfe", id="utf-16-le-bom"),
    pytest.param("utf-16-be", "UTF-16", b"\xfe\xff", id="utf-16-be-bom"),
    pytest.param("utf-16-le", "UTF-16LE", b"", id="utf-16-le"),
    pytest.param("utf-16-be", "UTF-16BE", b"", id="utf-16-be"),
    pytest.param("iso-8859-1", "ISO-8859-1", b"", id="latin-1"),
    pytest.param("ascii", "ASCII", b"", id="ascii"),
]


def encoded_xml(xml: bytes, codec: str, declaration: str, bom: bytes) -> bytes:
    """Change the encoding and its declaration together, leaving the XML content alone."""
    text = xml.decode("utf-8").replace('encoding="UTF-8"', f'encoding="{declaration}"')
    return bom + text.encode(codec)


def xbrl(*children: str) -> bytes:
    """Wrap chosen children in the shared namespaces, keeping their order available to occurrence-ID checks."""
    body = "\n".join(children)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="{XBRLI}"
             xmlns:xbrldi="{XBRLDI}"
             xmlns:us-gaap="{US_GAAP}"
             xmlns:iso4217="{ISO4217}"
             xmlns:xsi="{XSI}"
             xmlns:acme="https://example.com/acme">
{body}
</xbrli:xbrl>
""".encode()


def instant_context(context_id: str = "instant") -> str:
    """Give balance-sheet facts a single report-date instant."""
    return f"""<xbrli:context id="{context_id}">
  <xbrli:entity>
    <xbrli:identifier scheme="https://www.sec.gov/CIK">{CIK}</xbrli:identifier>
  </xbrli:entity>
  <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
</xbrli:context>"""


def duration_context(
    context_id: str,
    start: str,
    *,
    dimension: str = "",
    location: str = "segment",
) -> str:
    """Build a date range with an optional dimension in either segment or scenario."""
    segment = ""
    scenario = ""
    if dimension:
        wrapped = f"<xbrli:{location}>{dimension}</xbrli:{location}>"
        if location == "segment":
            segment = wrapped
        else:
            scenario = wrapped
    return f"""<xbrli:context id="{context_id}">
  <xbrli:entity>
    <xbrli:identifier scheme="https://www.sec.gov/CIK">{CIK}</xbrli:identifier>
    {segment}
  </xbrli:entity>
  <xbrli:period>
    <xbrli:startDate>{start}</xbrli:startDate>
    <xbrli:endDate>2024-09-28</xbrli:endDate>
  </xbrli:period>
  {scenario}
</xbrli:context>"""


def usd_unit() -> str:
    """Use the namespace-qualified USD measure expected by the extractor."""
    return """<xbrli:unit id="USD">
  <xbrli:measure>iso4217:USD</xbrli:measure>
</xbrli:unit>"""


def fixture(
    tmp_path: Path,
    xml: bytes,
    *,
    status: str = "COMPLETE",
    source_complete: bool = True,
    parser_ready: bool = True,
    document_name: str = DOCUMENT_NAME,
    description: str = "EXTRACTED XBRL INSTANCE DOCUMENT",
) -> tuple[Path, Path]:
    """Save the XML and discovery bytes with matching manifest sizes and hashes.

    This is a minimal local Bronze fixture, not a simulated SEC download.
    Tests can change readiness flags or a filename without bypassing the
    extractor's normal evidence checks.
    """
    filing_directory = tmp_path / "bronze" / f"cik={CIK}" / f"accession={ACCESSION}"
    metadata_directory = filing_directory / "metadata"
    derived_directory = filing_directory / "sec-derived"
    manifests_directory = filing_directory / "manifests"
    metadata_directory.mkdir(parents=True)
    derived_directory.mkdir()
    manifests_directory.mkdir()

    discovery = {
        "cik": CIK,
        "accession_number": ACCESSION,
        "index_url": "https://www.sec.gov/example-index.html",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "inventory": [
            {
                "section": "data-file",
                "document_name": document_name,
                "description": description,
                "sequence": "5",
                "document_type": "XML",
                "required_for_source": False,
            }
        ],
    }
    discovery_bytes = (json.dumps(discovery, indent=2) + "\n").encode()
    discovery_path = metadata_directory / "discovery.json"
    document_path = derived_directory / document_name
    discovery_path.write_bytes(discovery_bytes)
    document_path.write_bytes(xml)

    manifest = {
        "run_id": "test-run",
        "status": status,
        "source_complete": source_complete,
        "parser_ready": parser_ready,
        "cik": CIK,
        "accession_number": ACCESSION,
        "files": [
            evidence("discovery.json", "metadata", discovery_bytes),
            evidence(document_name, "data-file", xml),
        ],
    }
    manifest_path = manifests_directory / "run_id=test-run.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return filing_directory, manifest_path


def evidence(name: str, section: str, content: bytes) -> dict[str, object]:
    """Record the checksum of the actual bytes that the test is about to store."""
    return {
        "document_name": name,
        "section": section,
        "required_for_source": section == "metadata",
        "status": "VERIFIED",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_extracts_valid_instant_and_duration_facts(tmp_path: Path) -> None:
    xml = xbrl(
        instant_context(),
        duration_context("year", "2023-10-01"),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD" decimals="-6">352583000000</us-gaap:Assets>',
        '<us-gaap:Revenue contextRef="year" unitRef="USD" precision="10">391035000000</us-gaap:Revenue>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "COMPLETE"
    assert result.reference.cik == CIK
    assert result.selected_document_name == DOCUMENT_NAME
    assert result.selected_document_sha256 == hashlib.sha256(xml).hexdigest()
    assert (result.candidate_count, result.accepted_count, result.rejected_count) == (
        2,
        2,
        0,
    )
    instant, duration = result.accepted_facts
    assert instant.concept_namespace == US_GAAP
    assert instant.concept_local_name == "Assets"
    assert instant.value_decimal == Decimal(352583000000)
    assert instant.period_kind == "INSTANT"
    assert instant.period_instant == date(2024, 9, 28)
    assert instant.period_start is None
    assert instant.decimals == "-6"
    assert duration.period_kind == "DURATION"
    assert duration.period_start == date(2023, 10, 1)
    assert duration.period_end == date(2024, 9, 28)
    assert duration.precision == "10"
    assert duration.entity_identifier == CIK
    assert duration.entity_scheme == "https://www.sec.gov/CIK"
    assert duration.unit_expression == f"{{{ISO4217}}}USD"
    assert "context" in duration.context_xml
    assert "unit" in duration.unit_xml


def test_standard_xbrl_instance_description_is_usable(tmp_path: Path) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml, description="XBRL INSTANCE DOCUMENT")

    result = extract_filing_facts(directory, manifest)

    assert result.status == "COMPLETE"
    assert result.accepted_count == 1
    assert result.accepted_facts[0].value_decimal == Decimal(42)


@pytest.mark.parametrize("codec,declaration,bom", XML_ENCODINGS)
@pytest.mark.parametrize(
    "doctype",
    [
        "<!DOCTYPE xbrli:xbrl>",
        '<!DOCTYPE xbrli:xbrl [<!ENTITY amount "42">]>',
        '<!DOCTYPE xbrli:xbrl SYSTEM "https://example.invalid/unused.dtd">',
    ],
    ids=["empty-dtd", "internal-entity", "external-dtd"],
)
def test_dtd_rejected_in_supported_encodings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    codec: str,
    declaration: str,
    bom: bytes,
    doctype: str,
) -> None:
    value = "&amount;" if "<!ENTITY" in doctype else "42"
    xml = xbrl(
        instant_context(),
        usd_unit(),
        f'<us-gaap:Assets contextRef="instant" unitRef="USD">{value}</us-gaap:Assets>',
    ).replace(b"?>", ("?>\n" + doctype).encode(), 1)
    directory, manifest = fixture(tmp_path, encoded_xml(xml, codec, declaration, bom))

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("DTD rejection must not use the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    with pytest.raises(
        SilverExtractionError, match="must not contain a DTD or entities"
    ):
        extract_filing_facts(directory, manifest)


@pytest.mark.parametrize("codec,declaration,bom", XML_ENCODINGS)
def test_dtd_free_xml_in_supported_encodings(
    tmp_path: Path, codec: str, declaration: str, bom: bytes
) -> None:
    dimension = """<xbrldi:explicitMember xmlns:acme="https://example.com/local"
        dimension="acme:RegionAxis">acme:WholeCompany</xbrldi:explicitMember>"""
    xml = encoded_xml(
        xbrl(
            duration_context("year", "2023-10-01", dimension=dimension),
            usd_unit(),
            '<us-gaap:Revenue contextRef="year" unitRef="USD">12&#46;500</us-gaap:Revenue>',
            '<us-gaap:Revenue contextRef="year" unitRef="USD">12.500</us-gaap:Revenue>',
        ),
        codec,
        declaration,
        bom,
    )
    directory, manifest = fixture(tmp_path, xml)

    first = extract_filing_facts(directory, manifest)
    assert extract_filing_facts(directory, manifest) == first
    assert first.status == "COMPLETE"
    assert (first.candidate_count, first.accepted_count, first.rejected_count) == (
        2,
        2,
        0,
    )
    assert first.selected_document_sha256 == hashlib.sha256(xml).hexdigest()
    left, right = first.accepted_facts
    assert left.source_occurrence_id != right.source_occurrence_id
    assert left.value_decimal == right.value_decimal == Decimal("12.500")
    assert left.raw_value == right.raw_value == "12.500"
    assert left.unit_expression == right.unit_expression == f"{{{ISO4217}}}USD"
    assert left.period_start == right.period_start == date(2023, 10, 1)
    assert left.period_end == right.period_end == date(2024, 9, 28)
    assert len(first.dimensions) == 2
    assert [item.source_occurrence_id for item in first.dimensions] == [
        left.source_occurrence_id,
        right.source_occurrence_id,
    ]
    assert all(
        item.axis_namespace == "https://example.com/local"
        and item.member_value == "{https://example.com/local}WholeCompany"
        for item in first.dimensions
    )


@pytest.mark.parametrize("codec,declaration,bom", XML_ENCODINGS)
def test_malformed_xml_remains_an_extraction_failure(
    tmp_path: Path, codec: str, declaration: str, bom: bytes
) -> None:
    xml = b'<?xml version="1.0" encoding="UTF-8"?><xbrl>'
    directory, manifest = fixture(tmp_path, encoded_xml(xml, codec, declaration, bom))

    with pytest.raises(SilverExtractionError, match="Selected XML is not well-formed"):
        extract_filing_facts(directory, manifest)


@pytest.mark.parametrize("encoding", ["not-an-encoding", "Shift_JIS"])
def test_unsupported_xml_encoding_is_an_extraction_failure(
    tmp_path: Path, encoding: str
) -> None:
    xml = f'<?xml version="1.0" encoding="{encoding}"?><xbrl/>'.encode()
    directory, manifest = fixture(tmp_path, xml)

    with pytest.raises(SilverExtractionError, match="Selected XML is not well-formed"):
        extract_filing_facts(directory, manifest)


def test_parser_ready_partial_bronze_can_produce_complete_extraction(
    tmp_path: Path,
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">1</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml, status="PARTIAL")

    result = extract_filing_facts(directory, manifest)

    assert result.status == "COMPLETE"


def test_manifest_must_be_inside_the_canonical_filing_directory(
    tmp_path: Path,
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">1</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml)
    outside_manifest = tmp_path / "run_id=test-run.json"
    outside_manifest.write_bytes(manifest.read_bytes())

    with pytest.raises(SilverInputError, match="outside"):
        extract_filing_facts(directory, outside_manifest)


def test_directory_identity_must_match_the_manifest(tmp_path: Path) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">1</us-gaap:Assets>',
    )
    directory, _ = fixture(tmp_path, xml)
    wrong_directory = directory.rename(
        directory.parent / "accession=0000320193-24-999999"
    )
    manifest = wrong_directory / "manifests" / "run_id=test-run.json"

    with pytest.raises(SilverInputError, match="canonical path"):
        extract_filing_facts(wrong_directory, manifest)


def test_three_month_and_year_to_date_occurrences_remain_separate(
    tmp_path: Path,
) -> None:
    xml = xbrl(
        duration_context("quarter", "2024-06-30"),
        duration_context("year-to-date", "2023-10-01"),
        usd_unit(),
        '<us-gaap:Revenue contextRef="quarter" unitRef="USD">100</us-gaap:Revenue>',
        '<us-gaap:Revenue contextRef="year-to-date" unitRef="USD">300</us-gaap:Revenue>',
    )
    directory, manifest = fixture(tmp_path, xml)

    facts = extract_filing_facts(directory, manifest).accepted_facts

    assert [fact.period_start for fact in facts] == [
        date(2024, 6, 30),
        date(2023, 10, 1),
    ]
    assert [fact.value_decimal for fact in facts] == [Decimal(100), Decimal(300)]


def test_company_wide_explicit_and_typed_dimension_facts_remain_distinct(
    tmp_path: Path,
) -> None:
    explicit = (
        '<xbrldi:explicitMember dimension="acme:BusinessAxis">'
        "acme:ServicesMember</xbrldi:explicitMember>"
    )
    typed = (
        '<xbrldi:typedMember dimension="acme:RegionAxis">'
        "<acme:Region>Americas</acme:Region></xbrldi:typedMember>"
    )
    xml = xbrl(
        duration_context("company", "2023-10-01"),
        duration_context("segment", "2023-10-01", dimension=explicit),
        duration_context("typed", "2023-10-01", dimension=typed, location="scenario"),
        usd_unit(),
        '<us-gaap:Revenue contextRef="company" unitRef="USD">300</us-gaap:Revenue>',
        '<us-gaap:Revenue contextRef="segment" unitRef="USD">100</us-gaap:Revenue>',
        '<us-gaap:Revenue contextRef="typed" unitRef="USD">200</us-gaap:Revenue>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    counts = {
        fact.context_ref: sum(
            dimension.source_occurrence_id == fact.source_occurrence_id
            for dimension in result.dimensions
        )
        for fact in result.accepted_facts
    }
    assert counts == {"company": 0, "segment": 1, "typed": 1}
    explicit_result, typed_result = result.dimensions
    assert explicit_result.axis_namespace == "https://example.com/acme"
    assert explicit_result.axis_name == "BusinessAxis"
    assert explicit_result.member_kind == "EXPLICIT"
    assert explicit_result.member_value == "{https://example.com/acme}ServicesMember"
    assert explicit_result.context_location == "SEGMENT"
    assert typed_result.member_kind == "TYPED"
    assert typed_result.member_value == "Americas"
    assert typed_result.typed_member_xml is not None
    assert typed_result.context_location == "SCENARIO"


def test_equal_occurrences_keep_different_deterministic_source_ids(
    tmp_path: Path,
) -> None:
    """Two identical XML items are two pieces of evidence, with stable but different IDs."""
    fact = '<us-gaap:Revenue contextRef="year" unitRef="USD">100</us-gaap:Revenue>'
    xml = xbrl(duration_context("year", "2023-10-01"), usd_unit(), fact, fact)
    directory, manifest = fixture(tmp_path, xml)

    first = extract_filing_facts(directory, manifest)
    second = extract_filing_facts(directory, manifest)

    ids = [item.source_occurrence_id for item in first.accepted_facts]
    assert ids[0] != ids[1]
    assert ids == [item.source_occurrence_id for item in second.accepted_facts]
    assert (
        first.accepted_facts[0].source_ordinal != first.accepted_facts[1].source_ordinal
    )


def test_isolated_missing_context_and_bad_number_produce_partial_result(
    tmp_path: Path,
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
        '<us-gaap:Assets contextRef="missing" unitRef="USD">43</us-gaap:Assets>',
        '<us-gaap:Assets contextRef="instant" unitRef="USD">not-a-number</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL"
    assert (result.candidate_count, result.accepted_count, result.rejected_count) == (
        3,
        1,
        2,
    )
    assert [item.reason_code for item in result.rejected_occurrences] == [
        "CONTEXT_NOT_FOUND",
        "INVALID_VALUE",
    ]
    assert "missing" in result.rejected_occurrences[0].reason
    assert result.rejected_occurrences[1].raw_value == "not-a-number"
    assert result.rejected_occurrences[1].context_ref == "instant"


@pytest.mark.parametrize(
    ("old", "new", "reason_code"),
    [
        (
            f">{CIK}</xbrli:identifier>",
            f">{CIK}<acme:extra /></xbrli:identifier>",
            "INVALID_CONTEXT",
        ),
        (
            ">2024-09-28</xbrli:instant>",
            ">2024-09-28<acme:extra /></xbrli:instant>",
            "INVALID_PERIOD",
        ),
        (
            '<xbrli:context id="invalid">',
            '<xbrli:context id="invalid">stray',
            "INVALID_CONTEXT",
        ),
        (
            "</xbrli:period>\n</xbrli:context>",
            "</xbrli:period>stray\n</xbrli:context>",
            "INVALID_CONTEXT",
        ),
        ("<xbrli:entity>", "<xbrli:entity>stray", "INVALID_CONTEXT"),
        (
            "</xbrli:identifier>\n  </xbrli:entity>",
            "</xbrli:identifier>stray\n  </xbrli:entity>",
            "INVALID_CONTEXT",
        ),
        ("<xbrli:period>", "<xbrli:period>stray", "INVALID_PERIOD"),
        (
            "</xbrli:instant></xbrli:period>",
            "</xbrli:instant>stray</xbrli:period>",
            "INVALID_PERIOD",
        ),
    ],
)
def test_malformed_context_rejects_only_its_fact(
    tmp_path: Path, old: str, new: str, reason_code: str
) -> None:
    valid = instant_context("valid")
    original = instant_context("invalid")
    assert old in original
    invalid = original.replace(old, new, 1)
    xml = xbrl(
        valid,
        invalid,
        usd_unit(),
        '<us-gaap:Assets contextRef="valid" unitRef="USD">42</us-gaap:Assets>',
        '<us-gaap:Liabilities contextRef="invalid" unitRef="USD">7</us-gaap:Liabilities>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL"
    assert (result.candidate_count, result.accepted_count, result.rejected_count) == (
        2,
        1,
        1,
    )
    assert result.accepted_facts[0].concept_local_name == "Assets"
    assert result.accepted_facts[0].period_instant == date(2024, 9, 28)
    assert result.rejected_occurrences[0].concept_local_name == "Liabilities"
    assert result.rejected_occurrences[0].reason_code == reason_code


@pytest.mark.parametrize(
    "tag,value", [("startDate", "2023-10-01"), ("endDate", "2024-09-28")]
)
def test_nested_child_in_duration_date_rejects_only_affected_fact(
    tmp_path: Path, tag: str, value: str
) -> None:
    original = duration_context("invalid", "2023-10-01")
    old = f"<xbrli:{tag}>{value}</xbrli:{tag}>"
    assert old in original
    invalid = original.replace(
        old, f"<xbrli:{tag}>{value}<acme:extra /></xbrli:{tag}>", 1
    )
    xml = xbrl(
        instant_context("valid"),
        invalid,
        usd_unit(),
        '<us-gaap:Assets contextRef="valid" unitRef="USD">42</us-gaap:Assets>',
        '<us-gaap:Liabilities contextRef="invalid" unitRef="USD">7</us-gaap:Liabilities>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL"
    assert (result.candidate_count, result.accepted_count, result.rejected_count) == (
        2,
        1,
        1,
    )
    assert result.rejected_occurrences[0].reason_code == "INVALID_PERIOD"


def test_nil_is_accepted_and_out_of_range_decimal_is_rejected(tmp_path: Path) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">1</us-gaap:Assets>',
        '<us-gaap:Liabilities contextRef="instant" unitRef="USD" xsi:nil="true" />',
        '<us-gaap:Revenue contextRef="instant" unitRef="USD">100000000000000000000</us-gaap:Revenue>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL"
    nil_fact = next(fact for fact in result.accepted_facts if fact.is_nil)
    assert nil_fact.value_decimal is None
    assert nil_fact.raw_value == ""
    assert result.rejected_occurrences[0].reason_code == "DECIMAL_OUT_OF_RANGE"


def test_decimal_38_18_boundary_is_exact_without_rounding(tmp_path: Path) -> None:
    """The large value fits exactly; the tiny value needs a nineteenth fractional digit.

    Reject the tiny value instead of rounding it to zero. Decimal parsing
    must also leave the large value intact despite the default context precision.
    """
    largest = "99999999999999999999.999999999999999999"
    too_precise = "0.0000000000000000001"
    xml = xbrl(
        instant_context(),
        usd_unit(),
        f'<us-gaap:Assets contextRef="instant" unitRef="USD">{largest}</us-gaap:Assets>',
        f'<us-gaap:Revenue contextRef="instant" unitRef="USD">{too_precise}</us-gaap:Revenue>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.accepted_facts[0].value_decimal == Decimal(largest)
    assert result.rejected_occurrences[0].reason_code == "DECIMAL_OUT_OF_RANGE"


def test_child_inside_unit_measure_rejects_only_affected_fact(tmp_path: Path) -> None:
    valid_product = """<xbrli:unit id="PRODUCT">
  <xbrli:measure>iso4217:USD</xbrli:measure>
  <xbrli:measure>xbrli:shares</xbrli:measure>
</xbrli:unit>"""
    malformed = """<xbrli:unit id="MALFORMED">
  <xbrli:measure>iso4217:USD<acme:extra /></xbrli:measure>
</xbrli:unit>"""
    xml = xbrl(
        instant_context(),
        valid_product,
        malformed,
        '<us-gaap:Assets contextRef="instant" unitRef="PRODUCT">42</us-gaap:Assets>',
        '<us-gaap:Liabilities contextRef="instant" unitRef="MALFORMED">7</us-gaap:Liabilities>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL"
    assert (result.candidate_count, result.accepted_count, result.rejected_count) == (
        2,
        1,
        1,
    )
    assert result.accepted_facts[0].concept_local_name == "Assets"
    assert result.accepted_facts[0].unit_expression == (
        f"{{{ISO4217}}}USD * {{{XBRLI}}}shares"
    )
    assert result.rejected_occurrences[0].reason_code == "INVALID_UNIT"


def test_stray_text_in_unit_wrapper_rejects_only_affected_fact(tmp_path: Path) -> None:
    valid_divide = """<xbrli:unit id="USD_PER_SHARE">
  <xbrli:divide>
    <xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
    <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
  </xbrli:divide>
</xbrli:unit>"""
    malformed = """<xbrli:unit id="MALFORMED">
  <xbrli:divide>
    <xbrli:unitNumerator>unexpected<xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
    <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
  </xbrli:divide>
</xbrli:unit>"""
    xml = xbrl(
        instant_context(),
        valid_divide,
        malformed,
        '<us-gaap:EarningsPerShare contextRef="instant" unitRef="USD_PER_SHARE">1.25</us-gaap:EarningsPerShare>',
        '<us-gaap:OtherIncome contextRef="instant" unitRef="MALFORMED">7</us-gaap:OtherIncome>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL"
    assert (result.candidate_count, result.accepted_count, result.rejected_count) == (
        2,
        1,
        1,
    )
    assert result.accepted_facts[0].concept_local_name == "EarningsPerShare"
    assert result.accepted_facts[0].unit_expression == (
        f"({{{ISO4217}}}USD) / ({{{XBRLI}}}shares)"
    )
    assert result.rejected_occurrences[0].reason_code == "INVALID_UNIT"


@pytest.mark.parametrize("target", ["xml", "discovery"])
def test_tampered_bronze_bytes_are_rejected(tmp_path: Path, target: str) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">1</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml)
    path = (
        directory / "sec-derived" / DOCUMENT_NAME
        if target == "xml"
        else directory / "metadata" / "discovery.json"
    )
    path.write_bytes(path.read_bytes() + b"tampered")

    with pytest.raises(SilverInputError, match="does not match the manifest"):
        extract_filing_facts(directory, manifest)


@pytest.mark.parametrize(
    ("status", "source_complete", "parser_ready", "message"),
    [
        ("FAILED", False, False, "status"),
        ("COMPLETE", False, True, "source_complete"),
        ("PARTIAL", True, False, "parser_ready"),
    ],
)
def test_unusable_bronze_manifest_is_rejected(
    tmp_path: Path,
    status: str,
    source_complete: bool,
    parser_ready: bool,
    message: str,
) -> None:
    directory, manifest = fixture(
        tmp_path,
        b"<unused />",
        status=status,
        source_complete=source_complete,
        parser_ready=parser_ready,
    )

    with pytest.raises(SilverInputError, match=message):
        extract_filing_facts(directory, manifest)


def test_duplicate_shared_context_fails_the_filing(tmp_path: Path) -> None:
    xml = xbrl(
        instant_context("same"),
        instant_context("same"),
        usd_unit(),
        '<us-gaap:Assets contextRef="same" unitRef="USD">1</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml)

    with pytest.raises(SilverExtractionError, match="duplicate context"):
        extract_filing_facts(directory, manifest)


def test_nested_numeric_item_fails_instead_of_being_silently_skipped(
    tmp_path: Path,
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<acme:Tuple><us-gaap:Assets contextRef="instant" unitRef="USD">1</us-gaap:Assets></acme:Tuple>',
    )
    directory, manifest = fixture(tmp_path, xml)

    with pytest.raises(SilverExtractionError, match="below the root"):
        extract_filing_facts(directory, manifest)


@pytest.mark.parametrize("unit_attribute,unit_ref", [("", None), (' unitRef=""', "")])
def test_missing_unit_keeps_known_occurrence_and_root_ordinal(
    tmp_path: Path, unit_attribute: str, unit_ref: str | None
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
        '<acme:CompanyCode contextRef="instant">007</acme:CompanyCode>',
        f'<us-gaap:Liabilities contextRef="instant"{unit_attribute} acme:note="raw"> 7 </us-gaap:Liabilities>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL"
    assert (result.candidate_count, result.accepted_count, result.rejected_count) == (
        2,
        1,
        1,
    )
    assert result.accepted_facts[0].value_decimal == Decimal(42)
    assert result.accepted_facts[0].source_ordinal == 3
    rejection = result.rejected_occurrences[0]
    attributes = [("contextRef", "instant"), ("{https://example.com/acme}note", "raw")]
    if unit_ref is not None:
        attributes.append(("unitRef", unit_ref))
    assert rejection == RejectedOccurrence(
        source_occurrence_id=rejection.source_occurrence_id,
        source_ordinal=5,
        concept_namespace=US_GAAP,
        concept_local_name="Liabilities",
        raw_value=" 7 ",
        context_ref="instant",
        unit_ref=unit_ref,
        decimals=None,
        precision=None,
        raw_attributes=tuple(sorted(attributes)),
        reason_code="MISSING_UNIT_REF",
        reason="Numeric fact is missing unitRef",
    )
    assert (
        rejection.source_occurrence_id != result.accepted_facts[0].source_occurrence_id
    )
    assert extract_filing_facts(directory, manifest) == result


@pytest.mark.parametrize(
    "namespace,concept",
    [
        (US_GAAP, name)
        for name in (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
            "NetIncomeLoss",
            "EarningsPerShareDiluted",
            "Assets",
            "Liabilities",
            "CashAndCashEquivalentsAtCarryingValue",
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
        )
    ]
    + [
        ("http://xbrl.sec.gov/dei/2024", name)
        for name in (
            "EntityPublicFloat",
            "EntityCommonStockSharesOutstanding",
        )
    ],
)
def test_supported_numeric_concepts_are_identifiable_without_numeric_attributes(
    tmp_path: Path, namespace: str, concept: str
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
        f'<known:{concept} xmlns:known="{namespace}" contextRef="instant">7</known:{concept}>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL" and result.candidate_count == 2
    assert result.accepted_count == result.rejected_count == 1
    rejected = result.rejected_occurrences[0]
    assert (rejected.concept_namespace, rejected.concept_local_name) == (
        namespace,
        concept,
    )
    assert rejected.reason_code == "MISSING_UNIT_REF"


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("decimals", "-6"),
        ("precision", "10"),
        ("decimals", ""),
        ("precision", ""),
        ("decimals", "bad"),
        ("precision", "-1"),
    ],
)
def test_accuracy_attribute_presence_identifies_custom_numeric_items(
    tmp_path: Path, attribute: str, value: str
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
        f'<acme:Unlisted contextRef="instant" {attribute}="{value}">7</acme:Unlisted>',
    )
    directory, manifest = fixture(tmp_path, xml)

    result = extract_filing_facts(directory, manifest)

    assert result.status == "PARTIAL" and result.candidate_count == 2
    rejected = result.rejected_occurrences[0]
    assert rejected.reason_code == "MISSING_UNIT_REF"
    assert rejected.unit_ref is None and rejected.raw_value == "7"
    assert (rejected.decimals, rejected.precision) == (
        value if attribute == "decimals" else None,
        value if attribute == "precision" else None,
    )
    assert rejected.raw_attributes == tuple(
        sorted((("contextRef", "instant"), (attribute, value)))
    )
    assert extract_filing_facts(directory, manifest) == result


@pytest.mark.parametrize(
    "attributes,code",
    [("", "MISSING_CONTEXT_REF"), (' contextRef="missing"', "CONTEXT_NOT_FOUND")],
)
def test_missing_unit_preserves_context_rejection_precedence(
    tmp_path: Path, attributes: str, code: str
) -> None:
    directory, manifest = fixture(
        tmp_path,
        xbrl(
            instant_context(),
            usd_unit(),
            '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
            f"<us-gaap:Liabilities{attributes}>bad</us-gaap:Liabilities>",
        ),
    )
    result = extract_filing_facts(directory, manifest)
    assert result.status == "PARTIAL" and result.candidate_count == 2
    assert result.rejected_occurrences[0].reason_code == code


def test_known_nil_numeric_item_without_unit_is_rejected(tmp_path: Path) -> None:
    directory, manifest = fixture(
        tmp_path,
        xbrl(
            instant_context(),
            usd_unit(),
            '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
            '<us-gaap:Liabilities contextRef="instant" xsi:nil="true" />',
        ),
    )
    result = extract_filing_facts(directory, manifest)
    assert result.status == "PARTIAL" and result.candidate_count == 2
    rejected = result.rejected_occurrences[0]
    assert rejected.reason_code == "MISSING_UNIT_REF" and rejected.raw_value == ""
    assert (f"{{{XSI}}}nil", "true") in rejected.raw_attributes


@pytest.mark.parametrize(
    "namespace,concept",
    [
        ("https://example.com/acme", "Assets"),
        ("http://fasb.org/us-gaap/2024-extra", "Assets"),
        ("http://fasb.org/us-gaap/not-a-year", "Liabilities"),
        ("http://xbrl.sec.gov/dei/not-a-year", "EntityPublicFloat"),
        (US_GAAP, "RevenueAmount"),
        ("http://xbrl.sec.gov/dei/2024", "DocumentFiscalYearFocus"),
        ("https://example.com/acme", "CompanyCode"),
    ],
)
def test_numeric_looking_text_is_not_enough_to_identify_a_fact(
    tmp_path: Path, namespace: str, concept: str
) -> None:
    directory, manifest = fixture(
        tmp_path,
        xbrl(
            instant_context(),
            usd_unit(),
            '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
            f'<other:{concept} xmlns:other="{namespace}" contextRef="instant">2024</other:{concept}>',
            '<acme:Code xsi:nil="true" />',
            '<acme:Qualified acme:unitRef="USD" acme:decimals="-6" acme:precision="10">7</acme:Qualified>',
        ),
    )
    result = extract_filing_facts(directory, manifest)
    assert result.status == "COMPLETE"
    assert (result.candidate_count, result.accepted_count, result.rejected_count) == (
        1,
        1,
        0,
    )


@pytest.mark.parametrize(
    "namespace,concept",
    [
        ("http://fasb.org/us-gaap/2025", "Liabilities"),
        ("http://fasb.org/us-gaap/2011-01-31", "Assets"),
        ("http://xbrl.sec.gov/dei/2025", "EntityPublicFloat"),
        ("http://xbrl.sec.gov/dei/2014-01-31", "EntityCommonStockSharesOutstanding"),
    ],
)
def test_registry_recognizes_year_qualified_namespace_families(
    tmp_path: Path, namespace: str, concept: str
) -> None:
    directory, manifest = fixture(
        tmp_path,
        xbrl(
            instant_context(),
            usd_unit(),
            '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
            f'<known:{concept} xmlns:known="{namespace}" contextRef="instant">7</known:{concept}>',
        ),
    )
    result = extract_filing_facts(directory, manifest)
    assert result.status == "PARTIAL"
    assert result.rejected_occurrences[0].reason_code == "MISSING_UNIT_REF"


@pytest.mark.parametrize(
    "item",
    [
        '<us-gaap:Liabilities contextRef="instant">7</us-gaap:Liabilities>',
        '<acme:Unlisted contextRef="instant" decimals="">7</acme:Unlisted>',
        '<acme:Unlisted contextRef="instant" precision="bad">7</acme:Unlisted>',
        '<dei:EntityPublicFloat xmlns:dei="http://xbrl.sec.gov/dei/2024" xsi:nil="true" />',
    ],
)
def test_nested_identifiable_items_without_units_fail_the_filing(
    tmp_path: Path, item: str
) -> None:
    directory, manifest = fixture(
        tmp_path,
        xbrl(
            instant_context(),
            usd_unit(),
            '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
            f"<acme:Tuple><acme:Inner>{item}</acme:Inner></acme:Tuple>",
        ),
    )
    with pytest.raises(SilverExtractionError, match="below the root"):
        extract_filing_facts(directory, manifest)


@pytest.mark.parametrize(
    "payload",
    [
        "<us-gaap:Assets>7</us-gaap:Assets>",
        '<acme:Unlisted decimals="">7</acme:Unlisted>',
    ],
)
def test_typed_dimension_and_linkbase_payloads_are_not_fact_candidates(
    tmp_path: Path, payload: str
) -> None:
    dimension = (
        f'<xbrldi:typedMember dimension="acme:CodeAxis">{payload}</xbrldi:typedMember>'
    )
    directory, manifest = fixture(
        tmp_path,
        xbrl(
            duration_context("typed", "2023-10-01", dimension=dimension),
            usd_unit(),
            '<us-gaap:NetIncomeLoss contextRef="typed" unitRef="USD">42</us-gaap:NetIncomeLoss>',
            '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase">'
            '<acme:Metadata precision="bad"><us-gaap:Liabilities>7</us-gaap:Liabilities></acme:Metadata>'
            "</link:linkbase>",
        ),
    )
    result = extract_filing_facts(directory, manifest)
    assert result.status == "COMPLETE" and result.candidate_count == 1
    assert result.accepted_count == 1 and result.rejected_count == 0
    assert len(result.dimensions) == 1
    assert result.dimensions[0].member_kind == "TYPED"
    assert result.dimensions[0].member_value == "7"
    assert result.dimensions[0].typed_member_xml is not None
    assert extract_filing_facts(directory, manifest) == result


@pytest.mark.parametrize("attributes", ["", ' xsi:nil="true"'])
def test_identifiable_missing_unit_without_any_accepted_fact_still_fails(
    tmp_path: Path, attributes: str
) -> None:
    directory, manifest = fixture(
        tmp_path,
        xbrl(
            instant_context(),
            usd_unit(),
            f'<us-gaap:Liabilities contextRef="instant"{attributes} />',
        ),
    )
    with pytest.raises(
        SilverExtractionError, match="No non-nil numeric fact was accepted"
    ):
        extract_filing_facts(directory, manifest)


def test_no_non_nil_accepted_fact_fails_the_filing(tmp_path: Path) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD" xsi:nil="true" />',
    )
    directory, manifest = fixture(tmp_path, xml)

    with pytest.raises(SilverExtractionError, match="No non-nil"):
        extract_filing_facts(directory, manifest)


def test_unsafe_discovery_filename_is_rejected(tmp_path: Path) -> None:
    directory, manifest = fixture(
        tmp_path,
        b"<unused />",
        document_name="..%2Foutside.xml",
    )

    with pytest.raises(SilverInputError, match="Unsafe selected"):
        extract_filing_facts(directory, manifest)


def test_symlinked_selected_document_is_rejected(tmp_path: Path) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">1</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml)
    document = directory / "sec-derived" / DOCUMENT_NAME
    real_document = tmp_path / "real.xml"
    real_document.write_bytes(document.read_bytes())
    document.unlink()
    document.symlink_to(real_document)

    with pytest.raises(SilverInputError, match="symlink"):
        extract_filing_facts(directory, manifest)


def test_extraction_is_read_only_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">1</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml)
    before = tree_snapshot(tmp_path)

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Silver extraction must not use the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    result = extract_filing_facts(directory, manifest)

    assert tree_snapshot(tmp_path) == before
    with pytest.raises(FrozenInstanceError):
        result.status = "PARTIAL"  # type: ignore[misc]


def test_read_only_inspection_command_prints_compact_sample(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    xml = xbrl(
        instant_context(),
        usd_unit(),
        '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
    )
    directory, manifest = fixture(tmp_path, xml)

    assert inspect_main([str(directory), str(manifest)]) == 0

    output = capsys.readouterr().out
    assert "Status: COMPLETE" in output
    assert "Candidates: 1 | accepted: 1 | rejected: 0" in output
    assert DOCUMENT_NAME in output
    assert f"{{{US_GAAP}}}Assets = 42" in output
    assert "period=2024-09-28" in output
    assert "dimensions=0" in output


def tree_snapshot(root: Path) -> dict[str, bytes | None]:
    """Capture file bytes so read-only extraction can be compared with the original tree."""
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }
