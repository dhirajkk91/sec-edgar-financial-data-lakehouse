"""Print a read-only Gold snapshot and a small exact financial sample."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sec_edgar_lakehouse.dashboard_read import (
    DashboardReadError,
    load_dashboard_snapshot,
)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--cik")
    parser.add_argument("--accession-number")
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    try:
        snapshot = load_dashboard_snapshot(
            database_path=args.database,
            cik=args.cik,
            accession_number=args.accession_number,
        )
    except DashboardReadError as exc:
        print(f"Dashboard read failure: {exc}", file=sys.stderr)
        return 1
    print(f"Database: {snapshot.database_path}")
    print("Available CIKs: " + (", ".join(snapshot.available_ciks) or "none"))
    scope = f"CIK {snapshot.cik}" if snapshot.cik is not None else "all companies"
    if snapshot.accession_number is not None:
        scope += f" / accession {snapshot.accession_number}"
    print(f"Scope: {scope}")
    print(f"Filings: {len(snapshot.filings.rows)}")
    print(f"Financial metrics: {len(snapshot.financial_metrics.rows)}")
    print(f"Quality issues: {len(snapshot.quality_issues.rows)}")
    for row in snapshot.financial_metrics.rows[:5]:
        record = dict(zip(snapshot.financial_metrics.columns, row))
        print(
            f"Metric: {record['metric_code']} | Value: {record['value_decimal']} | "
            f"Period: {record['period_label']} | Start: {record['period_start']} | "
            f"End: {record['period_end']} | Instant: {record['period_instant']} | "
            f"Origin: {record['value_origin']} | Units: {record['unit_expression']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
