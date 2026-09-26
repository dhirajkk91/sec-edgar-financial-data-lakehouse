import hashlib
import json
import socket
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sec_edgar_lakehouse import (
    SilverExtractionError,
    SilverInputError,
    extract_filing_facts,
)
from sec_edgar_lakehouse.silver_inspect import main as inspect_main

CIK = "0000320193"
ACCESSION = "0000320193-24-000123"
DOCUMENT_NAME = "apple-20240928_htm.xml"
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
US_GAAP = "http://fasb.org/us-gaap/2024"
ISO4217 = "http://www.xbrl.org/2003/iso4217"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def xbrl(*children: str) -> bytes:
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
) -> tuple[Path, Path]:
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
                "description": "EXTRACTED XBRL INSTANCE DOCUMENT",
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
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }
