"""Load company profiles from one or more saved company-run records.

Use this to backfill silver.company_profiles for a database built before
company profiles existed. New pipeline runs load the profile automatically.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sec_edgar_lakehouse.metadata.company_profile import (
    CompanyProfileError,
    load_company_profile,
)


def main(arguments: Sequence[str] | None = None) -> int:
    """Load each run record in order; report every result and exit 1 if any failed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--run-record", required=True, type=Path, nargs="+")
    args = parser.parse_args(arguments)
    failed = 0
    for path in args.run_record:
        try:
            result = load_company_profile(
                run_record_path=path, database_path=args.database
            )
        except CompanyProfileError as exc:
            failed += 1
            print(f"FAILED {path}: {exc}", file=sys.stderr)
            continue
        profile = result.profile
        print(
            f"{result.outcome} | {profile.cik} | {profile.company_name} | "
            f"{profile.owner_org or '-'} | {profile.sic_description or '-'}"
        )
    print(
        f"Loaded {len(args.run_record) - failed} of {len(args.run_record)} run records"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
