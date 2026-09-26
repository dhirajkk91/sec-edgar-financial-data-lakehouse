"""Print a compact, read-only summary of one Silver extraction."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sec_edgar_lakehouse.silver_extraction import extract_filing_facts
from sec_edgar_lakehouse.silver_models import SilverFact


def _period(fact: SilverFact) -> str:
    if fact.period_kind == "INSTANT":
        return str(fact.period_instant)
    return f"{fact.period_start} to {fact.period_end}"


def main(argv: Sequence[str] | None = None) -> int:
    """Extract a filing and print status, counts, and up to five facts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filing_directory", type=Path)
    parser.add_argument("manifest_path", type=Path)
    arguments = parser.parse_args(argv)

    result = extract_filing_facts(arguments.filing_directory, arguments.manifest_path)
    print(f"Status: {result.status}")
    print(
        f"Candidates: {result.candidate_count} | accepted: {result.accepted_count} | "
        f"rejected: {result.rejected_count}"
    )
    print(
        f"Document: {result.selected_document_name} "
        f"(sha256={result.selected_document_sha256})"
    )
    dimension_counts: dict[str, int] = {}
    for dimension in result.dimensions:
        dimension_counts[dimension.source_occurrence_id] = (
            dimension_counts.get(dimension.source_occurrence_id, 0) + 1
        )
    for index, fact in enumerate(result.accepted_facts[:5], start=1):
        value = "nil" if fact.is_nil else str(fact.value_decimal)
        concept = f"{{{fact.concept_namespace}}}{fact.concept_local_name}"
        print(
            f"{index}. {concept} = {value} | period={_period(fact)} | "
            f"unit={fact.unit_expression} | "
            f"dimensions={dimension_counts.get(fact.source_occurrence_id, 0)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
