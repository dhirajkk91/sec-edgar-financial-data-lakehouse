import json
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import duckdb
import pytest

from sec_edgar_lakehouse import (
    FilingReference,
    RejectedOccurrence,
    SilverDimension,
    SilverExtraction,
    SilverFact,
    SilverWriteError,
    write_silver_parquet,
)
from sec_edgar_lakehouse.silver_parquet_inspect import main as inspect_main

REFERENCE = FilingReference("320193", "0000320193-24-000123")
DOCUMENT_NAME = "apple-20240928_htm.xml"
DOCUMENT_SHA256 = "a" * 64
CONCEPT_NAMESPACE = "http://fasb.org/us-gaap/2024"
UNIT_NAMESPACE = "http://www.xbrl.org/2003/iso4217"

FACTS_SCHEMA = (
    ("source_occurrence_id", "VARCHAR"),
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("source_document_name", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
    ("source_ordinal", "BIGINT"),
    ("processing_run_id", "VARCHAR"),
    ("parser_version", "VARCHAR"),
    ("schema_version", "VARCHAR"),
    ("concept_namespace", "VARCHAR"),
    ("concept_local_name", "VARCHAR"),
    ("raw_value", "VARCHAR"),
    ("value_decimal", "DECIMAL(38,18)"),
    ("is_nil", "BOOLEAN"),
    ("decimals", "VARCHAR"),
    ("precision", "VARCHAR"),
    ("unit_ref", "VARCHAR"),
    ("unit_expression", "VARCHAR"),
    ("unit_xml", "VARCHAR"),
    ("context_ref", "VARCHAR"),
    ("context_xml", "VARCHAR"),
    ("entity_scheme", "VARCHAR"),
    ("entity_identifier", "VARCHAR"),
    ("period_kind", "VARCHAR"),
    ("period_start", "DATE"),
    ("period_end", "DATE"),
    ("period_instant", "DATE"),
)

DIMENSIONS_SCHEMA = (
    ("source_occurrence_id", "VARCHAR"),
    ("axis_namespace", "VARCHAR"),
    ("axis_name", "VARCHAR"),
    ("member_kind", "VARCHAR"),
    ("member_value", "VARCHAR"),
    ("typed_member_xml", "VARCHAR"),
    ("context_location", "VARCHAR"),
)

REJECTIONS_SCHEMA = (
    ("source_occurrence_id", "VARCHAR"),
    ("cik", "VARCHAR"),
    ("accession_number", "VARCHAR"),
    ("source_document_name", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
    ("source_ordinal", "BIGINT"),
    ("processing_run_id", "VARCHAR"),
    ("parser_version", "VARCHAR"),
    ("schema_version", "VARCHAR"),
    ("concept_namespace", "VARCHAR"),
    ("concept_local_name", "VARCHAR"),
    ("raw_value", "VARCHAR"),
    ("context_ref", "VARCHAR"),
    ("unit_ref", "VARCHAR"),
    ("decimals", "VARCHAR"),
    ("precision", "VARCHAR"),
    ("raw_attributes_json", "VARCHAR"),
    ("reason_code", "VARCHAR"),
    ("reason", "VARCHAR"),
)


def fact(
    source_id: str,
    ordinal: int,
    value: Decimal | None,
    *,
    concept: str = "Revenue",
    context_ref: str = "duration",
) -> SilverFact:
    return SilverFact(
        source_occurrence_id=source_id,
        source_ordinal=ordinal,
        cik=REFERENCE.cik,
        accession_number=REFERENCE.accession_number,
        source_document_name=DOCUMENT_NAME,
        source_sha256=DOCUMENT_SHA256,
        concept_namespace=CONCEPT_NAMESPACE,
        concept_local_name=concept,
        raw_value="" if value is None else str(value),
        value_decimal=value,
        is_nil=value is None,
        decimals="-6",
        precision=None,
        context_ref=context_ref,
        context_xml=f'<context id="{context_ref}" />',
        entity_scheme="https://www.sec.gov/CIK",
        entity_identifier=REFERENCE.cik,
        period_kind="DURATION",
        period_start=date(2023, 10, 1),
        period_end=date(2024, 9, 28),
        period_instant=None,
        unit_ref="USD",
        unit_expression=f"{{{UNIT_NAMESPACE}}}USD",
        unit_xml='<unit id="USD" />',
    )


def extraction(
    facts: tuple[SilverFact, ...],
    *,
    dimensions: tuple[SilverDimension, ...] = (),
    rejections: tuple[RejectedOccurrence, ...] = (),
) -> SilverExtraction:
    return SilverExtraction(
        reference=REFERENCE,
        selected_document_name=DOCUMENT_NAME,
        selected_document_sha256=DOCUMENT_SHA256,
        status="PARTIAL" if rejections else "COMPLETE",
        accepted_facts=facts,
        dimensions=dimensions,
        rejected_occurrences=rejections,
        candidate_count=len(facts) + len(rejections),
        accepted_count=len(facts),
        rejected_count=len(rejections),
    )


def parquet_schema(path: Path) -> tuple[tuple[str, str], ...]:
    with duckdb.connect(database=":memory:") as connection:
        rows = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
        ).fetchall()
    return tuple((str(row[0]), str(row[1])) for row in rows)


def test_declared_schemas_exist_when_dimensions_and_rejections_are_empty(
    tmp_path: Path,
) -> None:
    output = tmp_path / "writer's output"

    written = write_silver_parquet(
        extraction((fact("fact-1", 4, Decimal("42.25")),)),
        output,
        processing_run_id="run-1",
    )

    assert parquet_schema(written.facts_path) == FACTS_SCHEMA
    assert parquet_schema(written.fact_dimensions_path) == DIMENSIONS_SCHEMA
    assert parquet_schema(written.rejected_facts_path) == REJECTIONS_SCHEMA
    assert written.facts_row_count == 1
    assert written.fact_dimensions_row_count == 0
    assert written.rejected_facts_row_count == 0
    assert written.fact_dimensions_path.is_file()
    assert written.rejected_facts_path.is_file()
    with pytest.raises(FrozenInstanceError):
        written.facts_row_count = 2  # type: ignore[misc]


def test_decimal_nil_zero_duplicates_and_source_order_round_trip_exactly(
    tmp_path: Path,
) -> None:
    largest = Decimal("99999999999999999999.999999999999999999")
    rows = (
        fact("maximum", 10, largest),
        fact("nil", 11, None, concept="Liabilities"),
        fact("zero", 12, Decimal(0), concept="Liabilities"),
        fact("duplicate-a", 13, Decimal(100)),
        fact("duplicate-b", 14, Decimal(100)),
    )
    written = write_silver_parquet(
        extraction(rows), tmp_path / "output", processing_run_id="run-exact"
    )

    with duckdb.connect(database=":memory:") as connection:
        actual = connection.execute(
            """
            SELECT source_occurrence_id, value_decimal, is_nil,
                   processing_run_id, parser_version, schema_version
            FROM read_parquet(?)
            """,
            [str(written.facts_path)],
        ).fetchall()

    assert [row[0] for row in actual] == [item.source_occurrence_id for item in rows]
    assert actual[0][1] == largest
    assert actual[1][1:3] == (None, True)
    assert actual[2][1:3] == (Decimal(0), False)
    assert actual[3][1] == actual[4][1] == Decimal(100)
    assert actual[3][0] != actual[4][0]
    assert {row[3:] for row in actual} == {("run-exact", "1", "1")}


def test_sql_distinguishes_dimensioned_and_undimensioned_occurrences(
    tmp_path: Path,
) -> None:
    undimensioned = fact("company-scope", 20, Decimal(300))
    dimensioned = fact("segment-scope", 21, Decimal(300))
    dimension = SilverDimension(
        source_occurrence_id=dimensioned.source_occurrence_id,
        axis_namespace="https://example.com/acme",
        axis_name="BusinessAxis",
        member_kind="EXPLICIT",
        member_value="{https://example.com/acme}ServicesMember",
        typed_member_xml=None,
        context_location="SEGMENT",
    )
    written = write_silver_parquet(
        extraction((undimensioned, dimensioned), dimensions=(dimension,)),
        tmp_path / "output",
        processing_run_id="run-dimensions",
    )

    with duckdb.connect(database=":memory:") as connection:
        rows = connection.execute(
            """
            SELECT facts.source_occurrence_id, count(dimensions.source_occurrence_id)
            FROM read_parquet(?) AS facts
            LEFT JOIN read_parquet(?) AS dimensions USING (source_occurrence_id)
            GROUP BY facts.source_occurrence_id, facts.source_ordinal
            ORDER BY facts.source_ordinal
            """,
            [str(written.facts_path), str(written.fact_dimensions_path)],
        ).fetchall()

    assert rows == [("company-scope", 0), ("segment-scope", 1)]


def test_partial_rejections_keep_source_identity_and_all_raw_attributes(
    tmp_path: Path,
) -> None:
    rejection = RejectedOccurrence(
        source_occurrence_id="rejected-1",
        source_ordinal=30,
        concept_namespace=CONCEPT_NAMESPACE,
        concept_local_name="Assets",
        raw_value="not-a-number",
        context_ref="instant",
        unit_ref="USD",
        decimals="INF",
        precision=None,
        raw_attributes=(("contextRef", "instant"), ("custom", "café")),
        reason_code="INVALID_VALUE",
        reason="Numeric value is not a decimal",
    )
    value = extraction((fact("accepted-1", 29, Decimal(1)),), rejections=(rejection,))

    written = write_silver_parquet(
        value, tmp_path / "output", processing_run_id="run-partial"
    )

    with duckdb.connect(database=":memory:") as connection:
        row = connection.execute(
            "SELECT * FROM read_parquet(?)", [str(written.rejected_facts_path)]
        ).fetchone()
    assert row is not None
    record = dict(zip((name for name, _ in REJECTIONS_SCHEMA), row, strict=True))
    assert record["cik"] == REFERENCE.cik
    assert record["accession_number"] == REFERENCE.accession_number
    assert record["source_document_name"] == DOCUMENT_NAME
    assert record["source_sha256"] == DOCUMENT_SHA256
    assert record["processing_run_id"] == "run-partial"
    assert record["parser_version"] == record["schema_version"] == "1"
    assert json.loads(record["raw_attributes_json"]) == [
        ["contextRef", "instant"],
        ["custom", "café"],
    ]
    assert record["reason_code"] == "INVALID_VALUE"


def test_existing_output_is_untouched(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("do not replace", encoding="utf-8")

    with pytest.raises(SilverWriteError, match="already exists"):
        write_silver_parquet(
            extraction((fact("fact-1", 1, Decimal(1)),)),
            output,
            processing_run_id="run-existing",
        )

    assert sentinel.read_text(encoding="utf-8") == "do not replace"
    assert list(output.iterdir()) == [sentinel]


def test_float_value_is_rejected_before_output_is_created(tmp_path: Path) -> None:
    output = tmp_path / "float-output"
    invalid_fact = replace(
        fact("float-value", 1, Decimal(1)),
        value_decimal=cast(Decimal, 1.0),
    )

    with pytest.raises(SilverWriteError, match="decimal.Decimal"):
        write_silver_parquet(
            extraction((invalid_fact,)),
            output,
            processing_run_id="run-float",
        )

    assert not output.exists()


def test_verification_failure_removes_only_the_new_output(tmp_path: Path) -> None:
    sibling = tmp_path / "keep.txt"
    sibling.write_text("keep", encoding="utf-8")
    first = fact("duplicate-id", 1, Decimal(1))
    second = replace(first, source_ordinal=2)
    output = tmp_path / "failed-output"

    with pytest.raises(SilverWriteError, match="must be unique"):
        write_silver_parquet(
            extraction((first, second)),
            output,
            processing_run_id="run-failure",
        )

    assert not output.exists()
    assert sibling.read_text(encoding="utf-8") == "keep"


def test_inspection_command_reports_counts_and_dimension_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = fact("plain", 1, Decimal(10))
    second = fact("segment", 2, Decimal(10))
    dimension = SilverDimension(
        source_occurrence_id="segment",
        axis_namespace="https://example.com/acme",
        axis_name="BusinessAxis",
        member_kind="EXPLICIT",
        member_value="{https://example.com/acme}ServicesMember",
        typed_member_xml=None,
        context_location="SEGMENT",
    )
    output = tmp_path / "output"
    write_silver_parquet(
        extraction((first, second), dimensions=(dimension,)),
        output,
        processing_run_id="run-inspect",
    )

    assert inspect_main([str(output)]) == 0

    displayed = capsys.readouterr().out
    assert "facts=2 dimensions=1 rejected=0" in displayed
    assert "dimensions=0" in displayed
    assert "dimensions=1" in displayed
    assert "company total" not in displayed.lower()
