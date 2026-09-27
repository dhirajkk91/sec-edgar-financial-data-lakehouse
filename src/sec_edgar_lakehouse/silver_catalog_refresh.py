"""Refresh a local DuckDB catalog over verified active Silver filings."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sec_edgar_lakehouse.silver_catalog import (
    SilverCatalogError,
    refresh_silver_catalog,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--silver-directory", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = refresh_silver_catalog(
            silver_directory=arguments.silver_directory,
            database_path=arguments.database,
        )
    except SilverCatalogError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Refresh status: {result.status}")
    print(f"Database: {result.database_path}")
    print(f"Active filings: {result.active_filing_count}")
    print(f"Catalog failures: {result.failure_count}")
    print(f"Facts: {result.facts_count}")
    print(f"Fact dimensions: {result.fact_dimensions_count}")
    print(f"Rejected facts: {result.rejected_facts_count}")
    print(
        "Relations: silver.active_filings, silver.facts, silver.fact_dimensions, "
        "silver.rejected_facts, silver.catalog_failures"
    )
    return 2 if result.status == "PARTIAL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
