"""Run the complete local company pipeline and persist its execution record."""

import argparse
import os
import re
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from sec_edgar_lakehouse.company_run import (
    CompanyRunError,
    _safe_error,
    execute_company_pipeline_run,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _date(value: str) -> date:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise argparse.ArgumentTypeError("Date must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Date must be a valid YYYY-MM-DD date"
        ) from exc


def main(arguments: Sequence[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--forms", required=True, nargs="+")
    for name in ("bronze-directory", "silver-directory", "database", "run-directory"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--filed-on-or-after", type=_date)
    parser.add_argument("--filed-on-or-before", type=_date)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--request-interval-seconds", type=float, default=0.5)
    args = parser.parse_args(arguments)
    user_agent = os.environ.get("SEC_USER_AGENT", "")
    if not user_agent.strip():
        print("FAILED: SEC_USER_AGENT must be set and non-blank", file=sys.stderr)
        return 1
    try:
        result = execute_company_pipeline_run(
            args.cik,
            user_agent=user_agent,
            forms=args.forms,
            bronze_directory=args.bronze_directory,
            silver_directory=args.silver_directory,
            database_path=args.database,
            run_directory=args.run_directory,
            processing_run_id=args.run_id if args.run_id is not None else uuid4().hex,
            filed_on_or_after=args.filed_on_or_after,
            filed_on_or_before=args.filed_on_or_before,
            limit=args.limit,
            request_interval_seconds=args.request_interval_seconds,
        )
    except CompanyRunError as exc:
        print(f"FAILED: {_safe_error(str(exc), user_agent)}", file=sys.stderr)
        return 1
    print(f"Run ID: {result.run_id}")
    print(f"Pipeline status: {result.status}")
    pipeline = result.pipeline_result
    if pipeline is not None:
        processing = pipeline.processing
        print(f"Company: {processing.discovery.entity_name}")
        print(f"Processing status: {processing.status}")
        print(
            f"Selected: {processing.selected_count} | Complete: {processing.complete_count} | "
            f"Partial: {processing.partial_count} | Skipped: {processing.skipped_count} | "
            f"Failed: {processing.failed_count} | Not attempted: {processing.not_attempted_count}"
        )
        print(f"Catalog status: {pipeline.catalog_status}")
        catalog = pipeline.catalog_result
        if catalog is not None:
            print(f"Database: {catalog.database_path}")
            print(
                f"Facts: {catalog.facts_count} | Fact dimensions: {catalog.fact_dimensions_count} | Rejected facts: {catalog.rejected_facts_count}"
            )
        if pipeline.catalog_error:
            print(
                f"Catalog error: {_safe_error(pipeline.catalog_error, user_agent)}",
                file=sys.stderr,
            )
    else:
        print("Catalog status: SKIPPED")
    print(f"Run record: {result.run_record_path}")
    if result.error_message is not None:
        print(
            f"FAILED ({result.error_stage}): {_safe_error(result.error_message, user_agent)}",
            file=sys.stderr,
        )
    return {"COMPLETE": 0, "PARTIAL": 2, "FAILED": 1}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
