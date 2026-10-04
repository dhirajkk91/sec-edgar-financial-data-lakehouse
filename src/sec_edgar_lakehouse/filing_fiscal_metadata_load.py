"""Load verified fiscal metadata into an existing local DuckDB catalog."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sec_edgar_lakehouse.filing_fiscal_metadata_storage import (
    FiscalMetadataLoadError,
    load_filing_fiscal_metadata,
)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filing_directory", type=Path)
    parser.add_argument("manifest_path", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args(arguments)
    try:
        result = load_filing_fiscal_metadata(
            args.filing_directory, args.manifest_path, database_path=args.database
        )
    except FiscalMetadataLoadError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    metadata = result.metadata
    print(f"Outcome: {result.outcome} | Status: {result.status}")
    print(f"Database: {result.database_path}")
    print(f"Filing: {metadata.reference.cik} / {metadata.reference.accession_number}")
    print(
        f"Document: {metadata.selected_document_name} | SHA-256: {metadata.selected_document_sha256}"
    )
    print(f"Extraction version: {metadata.extraction_version}")
    print(
        f"Fiscal year: {metadata.fiscal_year_focus} | Fiscal focus: {metadata.fiscal_period_focus} | Document end: {metadata.document_period_end_date}"
    )
    print(f"Occurrences: {len(metadata.occurrences)} | Issues: {len(metadata.issues)}")
    for issue in metadata.issues:
        print(
            f"{issue.field_name} | {issue.reason_code}: {issue.message} | IDs: {', '.join(issue.source_occurrence_ids)}"
        )
    return 0 if result.status == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
