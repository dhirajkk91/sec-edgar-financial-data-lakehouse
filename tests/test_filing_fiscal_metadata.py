import hashlib
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import duckdb
import pytest
from test_silver_extraction import (
    ACCESSION,
    CIK,
    DOCUMENT_NAME,
    duration_context,
    fixture,
    xbrl,
)

from sec_edgar_lakehouse import (
    FiscalMetadataExtractionError,
    FiscalMetadataInputError,
    extract_filing_fiscal_metadata,
)
from sec_edgar_lakehouse.silver_extraction import (
    _source_occurrence_id,
    _verify_bronze_input,
)

DEI = "http://xbrl.sec.gov/dei/2024"
CONCEPTS = (
    "DocumentFiscalYearFocus",
    "DocumentFiscalPeriodFocus",
    "DocumentPeriodEndDate",
)


def fact(
    concept: str,
    value: str,
    *,
    context: str = "year",
    attrs: str = "",
    namespace: str = DEI,
    prefix: str = "focus",
) -> str:
    return f'<{prefix}:{concept} xmlns:{prefix}="{namespace}" contextRef="{context}" {attrs}>{value}</{prefix}:{concept}>'


def document(
    *extra: str,
    year: str = "2024",
    focus: str = "FY",
    end: str = "2024-09-28",
    context: str | None = None,
) -> bytes:
    return xbrl(
        context if context is not None else duration_context("year", "2023-10-01"),
        fact(CONCEPTS[0], year),
        fact(CONCEPTS[1], focus),
        fact(CONCEPTS[2], end),
        *extra,
    )


def make_fixture(
    tmp_path: Path, xml: bytes, *, form: str = "10-K"
) -> tuple[Path, Path, Path]:
    directory, manifest = fixture(tmp_path, xml)
    database = tmp_path / "catalog.duckdb"
    with duckdb.connect(str(database)) as conn:
        conn.execute("CREATE SCHEMA silver")
        conn.execute(
            "CREATE TABLE silver.active_filings (cik VARCHAR, accession_number VARCHAR, source_document_name VARCHAR, source_sha256 VARCHAR)"
        )
        conn.execute(
            "CREATE TABLE silver.filing_metadata (cik VARCHAR, accession_number VARCHAR, form VARCHAR, report_date DATE, metadata_run_id VARCHAR, submissions_sha256 VARCHAR)"
        )
        conn.execute(
            "INSERT INTO silver.active_filings VALUES (?, ?, ?, ?)",
            [CIK, ACCESSION, DOCUMENT_NAME, hashlib.sha256(xml).hexdigest()],
        )
        conn.execute(
            "INSERT INTO silver.filing_metadata VALUES (?, ?, ?, ?, ?, ?)",
            [CIK, ACCESSION, form, date(2024, 9, 28), "metadata-run", "a" * 64],
        )
    return directory, manifest, database


def extract(paths: tuple[Path, Path, Path]):
    return extract_filing_fiscal_metadata(paths[0], paths[1], database_path=paths[2])


@pytest.mark.parametrize(
    "form,focus", [("10-K", "FY"), ("10-K/A", "FY"), ("10-Q", "Q1"), ("10-Q/A", "Q3")]
)
def test_valid_forms_and_read_only_evidence(
    tmp_path: Path, form: str, focus: str
) -> None:
    paths = make_fixture(tmp_path, document(year=" 2023\n", focus=focus), form=form)
    files = [*paths[0].rglob("*"), paths[2]]
    before = {p: p.read_bytes() for p in files if p.is_file()}
    result = extract(paths)
    assert result.status == "COMPLETE"
    assert (
        result.fiscal_year_focus == 2023
    )  # A report's calendar year is not its fiscal focus.
    assert result.fiscal_period_focus == focus
    assert result.document_period_end_date == date(2024, 9, 28)
    assert result.extraction_version == "1"
    assert result.metadata_run_id == "metadata-run"
    assert result.submissions_sha256 == "a" * 64
    assert result.occurrences[0].raw_value == " 2023\n"
    assert result.occurrences[0].period_start == date(2023, 10, 1)
    assert result.occurrences[0].context_xml is not None
    assert [o.source_ordinal for o in result.occurrences] == [2, 3, 4]
    verified = _verify_bronze_input(paths[0], paths[1])
    assert result.occurrences[0].source_occurrence_id == _source_occurrence_id(
        verified, 2
    )
    assert extract(paths) == result
    assert before == {p: p.read_bytes() for p in before}
    for record in (result, result.occurrences[0]):
        assert not hasattr(record, "__dict__")
        with pytest.raises(FrozenInstanceError):
            record.source_ordinal = 1
    assert isinstance(result.occurrences, tuple) and isinstance(result.issues, tuple)


def test_agreement_prefix_and_invalid_occurrence_retention(tmp_path: Path) -> None:
    paths = make_fixture(
        tmp_path,
        document(
            fact(CONCEPTS[0], "2024", prefix="other", namespace=DEI + "-01-31"),
            fact(CONCEPTS[0], "bad"),
        ),
    )
    result = extract(paths)
    assert result.fiscal_year_focus == 2024 and result.status == "PARTIAL"
    assert len(result.occurrences) == 5
    assert result.occurrences[-1].normalized_value is None
    assert result.occurrences[-1].context_xml is not None
    assert [i.reason_code for i in result.issues] == ["INVALID_FISCAL_FIELD"]
    assert not hasattr(result.issues[0], "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.issues[0].message = "changed"


@pytest.mark.parametrize(
    "concept,value,form,focus,field",
    [
        (CONCEPTS[0], "2023", "10-K", "FY", "fiscal_year_focus"),
        (CONCEPTS[1], "Q2", "10-Q", "Q1", "fiscal_period_focus"),
    ],
)
def test_conflicting_values_resolve_other_fields(
    tmp_path: Path, concept: str, value: str, form: str, focus: str, field: str
) -> None:
    result = extract(
        make_fixture(tmp_path, document(fact(concept, value), focus=focus), form=form)
    )
    assert getattr(result, field) is None and result.status == "PARTIAL"
    assert result.document_period_end_date == date(2024, 9, 28)
    assert result.issues[0].reason_code == "CONFLICTING_FISCAL_FIELD"
    assert len(result.issues[0].source_occurrence_ids) == 2


@pytest.mark.parametrize(
    "concept,value,attrs,code",
    [
        (CONCEPTS[0], "", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[0], "0000", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[0], "２０２４", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[0], "20240", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[0], "2024", 'xsi:nil="true"', "INVALID_FISCAL_FIELD"),
        (CONCEPTS[0], "<child>2024</child>", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[1], "Q4", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[1], "fy", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[1], "Q1", "", "FISCAL_PERIOD_FORM_MISMATCH"),
        (CONCEPTS[2], "2024-9-28", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[2], "2024-02-30", "", "INVALID_FISCAL_FIELD"),
        (CONCEPTS[2], "2024-09-27", "", "REPORT_DATE_MISMATCH"),
    ],
)
def test_invalid_values_have_no_redundant_missing_issue(
    tmp_path: Path, concept: str, value: str, attrs: str, code: str
) -> None:
    xml = xbrl(
        duration_context("year", "2023-10-01"), fact(concept, value, attrs=attrs)
    )
    result = extract(make_fixture(tmp_path, xml))
    assert result.occurrences[0].normalized_value is None
    assert result.issues[0].reason_code == code
    assert (
        len([i for i in result.issues if i.reason_code == "MISSING_FISCAL_FIELD"]) == 2
    )


@pytest.mark.parametrize(
    "kind",
    [
        "entity",
        "space",
        "scheme",
        "segment",
        "scenario",
        "reversed",
        "invalid-date",
        "instant",
        "context-mismatch",
        "missing-context",
    ],
)
def test_unusable_context_retains_safe_evidence(tmp_path: Path, kind: str) -> None:
    context = duration_context("year", "2023-10-01")
    if kind == "entity":
        context = context.replace(CIK, "123")
    if kind == "space":
        context = context.replace(CIK, CIK + " ")
    if kind == "scheme":
        context = context.replace("https://www.sec.gov/CIK", "urn:company")
    if kind == "segment":
        context = context.replace("</xbrli:entity>", "<xbrli:segment/></xbrli:entity>")
    if kind == "scenario":
        context = context.replace(
            "</xbrli:context>", "<xbrli:scenario/></xbrli:context>"
        )
    if kind == "reversed":
        context = context.replace("2023-10-01", "2025-01-01")
    if kind == "invalid-date":
        context = context.replace("2023-10-01", "2023-02-30")
    if kind == "instant":
        context = context.replace(
            "<xbrli:startDate>2023-10-01</xbrli:startDate>", ""
        ).replace("endDate", "instant")
    if kind == "context-mismatch":
        context = context.replace("2024-09-28", "2024-09-27")
    if kind == "missing-context":
        context = context.replace('id="year"', 'id="other"')
    result = extract(make_fixture(tmp_path, document(context=context)))
    assert result.status == "PARTIAL" and result.fiscal_year_focus is None
    code = (
        "REPORT_DATE_MISMATCH" if kind == "context-mismatch" else "INVALID_FISCAL_FIELD"
    )
    assert {i.reason_code for i in result.issues} == {code}
    assert (
        result.occurrences[0].context_xml is not None
        if kind != "missing-context"
        else result.occurrences[0].context_xml is None
    )


@pytest.mark.parametrize(
    "namespace",
    [
        "https://company.example/dei/2024",
        "http://xbrl.sec.gov/dei/2024/extension",
        "http://example.com/dei/2024",
        "http://xbrl.sec.gov/dei/2024junk",
    ],
)
def test_namespace_full_match_and_missing_fields(
    tmp_path: Path, namespace: str
) -> None:
    result = extract(
        make_fixture(
            tmp_path,
            xbrl(
                duration_context("year", "2023-10-01"),
                fact(CONCEPTS[0], "2024", namespace=namespace),
            ),
        )
    )
    assert result.occurrences == ()
    assert len(result.issues) == 3
    assert {i.reason_code for i in result.issues} == {"MISSING_FISCAL_FIELD"}


@pytest.mark.parametrize(
    "kind", ["malformed", "dtd", "wrong-root", "duplicate-context", "nested"]
)
def test_shared_xml_failures(tmp_path: Path, kind: str) -> None:
    xml = document()
    if kind == "malformed":
        xml = b"<broken"
    if kind == "dtd":
        xml = b'<!DOCTYPE x [<!ENTITY v "2024">]>' + xml
    if kind == "wrong-root":
        xml = b"<document/>"
    if kind == "duplicate-context":
        xml = document(duration_context("year", "2023-10-01"))
    if kind == "nested":
        xml = document("<wrapper>" + fact(CONCEPTS[0], "2024") + "</wrapper>")
    with pytest.raises(FiscalMetadataExtractionError):
        extract(make_fixture(tmp_path, xml))


def test_tampered_bronze_reuses_verifier(tmp_path: Path) -> None:
    paths = make_fixture(tmp_path, document())
    (paths[0] / "sec-derived" / DOCUMENT_NAME).write_bytes(b"changed")
    with pytest.raises(FiscalMetadataInputError) as caught:
        extract(paths)
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    "kind",
    [
        "missing-db",
        "directory",
        "bad-db",
        "missing-relation",
        "missing-active",
        "missing-metadata",
        "duplicate-active",
        "duplicate-metadata",
        "checksum",
        "document",
        "report-date",
        "form",
        "run-id",
        "submissions",
    ],
)
def test_catalog_input_failures(tmp_path: Path, kind: str) -> None:
    paths = make_fixture(tmp_path, document())
    db = paths[2]
    if kind == "missing-db":
        db = tmp_path / "absent" / "database.duckdb"
    elif kind == "directory":
        db = tmp_path
    elif kind == "bad-db":
        db = tmp_path / "bad.db"
        db.write_bytes(b"bad database")
    else:
        with duckdb.connect(str(db)) as conn:
            sql = {
                "missing-relation": "DROP TABLE silver.filing_metadata",
                "missing-active": "DELETE FROM silver.active_filings",
                "missing-metadata": "DELETE FROM silver.filing_metadata",
                "duplicate-active": "INSERT INTO silver.active_filings SELECT * FROM silver.active_filings",
                "duplicate-metadata": "INSERT INTO silver.filing_metadata SELECT * FROM silver.filing_metadata",
                "checksum": "UPDATE silver.active_filings SET source_sha256='wrong'",
                "document": "UPDATE silver.active_filings SET source_document_name='wrong.xml'",
                "report-date": "UPDATE silver.filing_metadata SET report_date=NULL",
                "form": "UPDATE silver.filing_metadata SET form='8-K'",
                "run-id": "UPDATE silver.filing_metadata SET metadata_run_id=NULL",
                "submissions": "UPDATE silver.filing_metadata SET submissions_sha256=NULL",
            }[kind]
            conn.execute(sql)
    with pytest.raises(FiscalMetadataInputError):
        extract((paths[0], paths[1], db))
    assert not (tmp_path / "absent").exists()


def test_database_connection_failure_and_programming_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = make_fixture(tmp_path, document())
    with (
        duckdb.connect(str(paths[2])),
        pytest.raises(FiscalMetadataInputError) as caught,
    ):
        extract(paths)
    assert isinstance(caught.value.__cause__, duckdb.Error)
    from sec_edgar_lakehouse import filing_fiscal_metadata as module

    def fail(*args: object) -> None:
        raise RuntimeError("programming bug")

    monkeypatch.setattr(module, "_normalize", fail)
    with pytest.raises(RuntimeError, match="programming bug"):
        extract(paths)


@pytest.mark.parametrize("error", [ValueError, TypeError])
def test_unrelated_context_programming_errors_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    from sec_edgar_lakehouse import filing_fiscal_metadata as module

    paths = make_fixture(tmp_path, document())

    def fail(*args: object) -> None:
        raise error("programming bug")

    monkeypatch.setattr(module, "_parse_context", fail)
    with pytest.raises(error, match="programming bug"):
        extract(paths)
