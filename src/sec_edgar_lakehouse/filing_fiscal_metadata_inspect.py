"""Print a read-only summary of verified filing fiscal metadata."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sec_edgar_lakehouse.filing_fiscal_metadata import (
    _FIELDS,
    FiscalMetadataExtractionError,
    FiscalMetadataInputError,
    extract_filing_fiscal_metadata,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filing_directory", type=Path)
    parser.add_argument("manifest_path", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = extract_filing_fiscal_metadata(
            args.filing_directory, args.manifest_path, database_path=args.database
        )
    except (FiscalMetadataInputError, FiscalMetadataExtractionError) as exc:
        print(f"Fiscal metadata failure: {exc}")
        return 1
    print(
        f"Filing: {result.reference.cik} / {result.reference.accession_number} | Status: {result.status}"
    )
    print(
        f"Document: {result.selected_document_name} | SHA-256: {result.selected_document_sha256}"
    )
    for concept, field in _FIELDS.items():
        value = getattr(result, field)
        print(f"{field}: {value}")
        if value is not None:
            ids = [
                o.source_occurrence_id
                for o in result.occurrences
                if o.concept_local_name == concept and o.normalized_value is not None
            ]
            print("Supporting occurrence IDs: " + ", ".join(ids))
    for issue in result.issues:
        print(
            f"Issue: {issue.field_name} | {issue.reason_code} | {issue.message} | IDs: {', '.join(issue.source_occurrence_ids)}"
        )
    return 0 if result.status == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
