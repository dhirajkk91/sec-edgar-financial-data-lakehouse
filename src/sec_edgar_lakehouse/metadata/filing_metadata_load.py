"""Load verified filing metadata from one company-run record."""

# The company-run record supplies the saved submissions evidence.
# This command loads dates and forms only for active filings in the chosen database.

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from sec_edgar_lakehouse.metadata.filing_metadata import (
    FilingMetadataError,
    load_filing_metadata,
)


class _Parser(argparse.ArgumentParser):
    """Keep argument errors on the same exit code as a failed metadata load."""

    def error(self, message: str) -> NoReturn:
        """Use the same failure exit code for bad arguments and failed metadata loading."""
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def main(arguments: Sequence[str] | None = None) -> int:
    """Load verified filing metadata and print counts, issues and the final status."""
    parser = _Parser(description=__doc__)
    parser.add_argument("--run-record", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args(arguments)
    try:
        result = load_filing_metadata(
            run_record_path=args.run_record, database_path=args.database
        )
    except FilingMetadataError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Status: {result.status}")
    print(f"Company CIK: {result.cik}")
    print(f"Source run ID: {result.source_run_id}")
    print(f"Database: {result.database_path}")
    print(
        f"Active filings: {result.active_filing_count} | Inserted: {result.inserted_count} | Already existing: {result.already_existing_count} | Missing: {result.missing_count} | Conflicts: {result.conflict_count}"
    )
    for issue in result.issues:
        print(f"{issue.accession_number} | {issue.reason_code}: {issue.message}")
    return 2 if result.status == "PARTIAL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
