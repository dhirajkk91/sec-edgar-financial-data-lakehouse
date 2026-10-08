"""Run the end-to-end pipeline and publish its finalized summary."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from sec_edgar_lakehouse.company_pipeline_run import _date
from sec_edgar_lakehouse.company_run import _safe_error
from sec_edgar_lakehouse.end_to_end_pipeline import EndToEndPipelineResult
from sec_edgar_lakehouse.end_to_end_run import (
    EndToEndRunError,
    execute_end_to_end_pipeline_run,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        safe = _safe_error(message, os.environ.get("SEC_USER_AGENT", ""))
        self.exit(1, f"{self.prog}: error: {safe}\n")


def _print_pipeline(result: EndToEndPipelineResult, user_agent: str) -> None:
    def show(message: str, *, error: bool = False) -> None:
        print(
            _safe_error(message, user_agent), file=sys.stderr if error else sys.stdout
        )

    company = result.company_run
    company_status = (
        company.status
        if company
        else "FAILED"
        if result.company_error
        else "unavailable"
    )
    show(f"Company status: {company_status}")
    show(f"Metadata status: {result.metadata_status}")
    show(f"dbt status: {result.dbt_status}")
    show(f"Database: {result.database_path}")
    for label, message in (
        ("Company error", result.company_error),
        ("Metadata error", result.metadata_error),
        ("dbt error", result.dbt_error),
        ("Metadata skipped", result.metadata_skip_reason),
        ("dbt skipped", result.dbt_skip_reason),
    ):
        if message:
            show(f"{label}: {message}", error=True)
    if company:
        show(f"Company record: {company.run_record_path}")
        if company.error_message:
            show(
                f"Company error ({company.error_stage}): {company.error_message}",
                error=True,
            )
        pipeline = company.pipeline_result
        if pipeline:
            processing = pipeline.processing
            show(f"Company: {processing.discovery.entity_name}")
            show(f"Processing status: {processing.status}")
            show(
                f"Selected: {processing.selected_count} | Complete: {processing.complete_count} | "
                f"Partial: {processing.partial_count} | Skipped: {processing.skipped_count} | "
                f"Failed: {processing.failed_count} | Not attempted: {processing.not_attempted_count} | "
                f"Usable: {processing.usable_count}"
            )
            show(f"Catalog status: {pipeline.catalog_status}")
            if pipeline.catalog_error:
                show(f"Catalog error: {pipeline.catalog_error}", error=True)
    metadata = result.metadata_result
    if metadata:
        if metadata.filing_metadata_error:
            show(f"Filing metadata error: {metadata.filing_metadata_error}", error=True)
        filing = metadata.filing_metadata_result
        if filing:
            show(
                f"Filing metadata: {filing.status} | Inserted: {filing.inserted_count} | "
                f"Already existing: {filing.already_existing_count} | Missing: {filing.missing_count} | "
                f"Conflicts: {filing.conflict_count}"
            )
            for issue in filing.issues:
                show(
                    f"{issue.accession_number} | {issue.reason_code}: {issue.message}",
                    error=True,
                )
        for item in metadata.fiscal_results:
            show(
                f"Fiscal metadata: {item.reference.accession_number} | {item.outcome} | {item.status}"
            )
            if item.error_message:
                show(
                    f"Fiscal error ({item.error_stage}): {item.error_message}",
                    error=True,
                )
            if item.load_result:
                for fiscal_issue in item.load_result.metadata.issues:
                    show(
                        f"Fiscal issue {fiscal_issue.field_name} | {fiscal_issue.reason_code}: {fiscal_issue.message}",
                        error=True,
                    )
    if result.dbt_artifact_directory is not None:
        show(f"dbt artifacts: {result.dbt_artifact_directory}")
    build = result.dbt_result
    if build:
        show(f"dbt invocation: {build.invocation_id}")
        show(
            "dbt nodes: "
            + ", ".join(
                f"{status}={count}" for status, count in build.node_status_counts
            )
        )
        for label, path in (
            ("dbt stdout", build.stdout_path),
            ("dbt stderr", build.stderr_path),
            ("dbt manifest", build.manifest_path),
            ("dbt run results", build.run_results_path),
        ):
            if path is not None:
                show(f"{label}: {path}")
        if build.error_message:
            show(f"dbt build error: {build.error_message}", error=True)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--forms", required=True, nargs="+")
    for name in (
        "bronze-directory",
        "silver-directory",
        "database",
        "run-directory",
        "end-to-end-run-directory",
        "dbt-project-directory",
        "dbt-profiles-directory",
        "dbt-artifact-directory",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--filed-on-or-after", type=_date)
    parser.add_argument("--filed-on-or-before", type=_date)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--request-interval-seconds", type=float, default=0.5)
    parser.add_argument("--dbt-timeout-seconds", type=float, default=600.0)
    args = parser.parse_args(arguments)
    user_agent = os.environ.get("SEC_USER_AGENT", "")
    if not user_agent.strip():
        print("FAILED: SEC_USER_AGENT must be set and non-blank", file=sys.stderr)
        return 1
    run_id = args.run_id if args.run_id is not None else uuid4().hex
    try:
        result = execute_end_to_end_pipeline_run(
            args.cik,
            user_agent=user_agent,
            forms=args.forms,
            bronze_directory=args.bronze_directory,
            silver_directory=args.silver_directory,
            database_path=args.database,
            processing_run_id=run_id,
            run_directory=args.run_directory,
            end_to_end_run_directory=args.end_to_end_run_directory,
            dbt_project_directory=args.dbt_project_directory,
            dbt_profiles_directory=args.dbt_profiles_directory,
            dbt_artifact_directory=args.dbt_artifact_directory,
            filed_on_or_after=args.filed_on_or_after,
            filed_on_or_before=args.filed_on_or_before,
            limit=args.limit,
            request_interval_seconds=args.request_interval_seconds,
            dbt_timeout_seconds=args.dbt_timeout_seconds,
        )
    except EndToEndRunError as exc:
        print(_safe_error(f"Run ID: {run_id}", user_agent))
        print(_safe_error(f"FAILED: {exc}", user_agent), file=sys.stderr)
        if exc.pipeline_result is not None:
            _print_pipeline(exc.pipeline_result, user_agent)
        if exc.run_record_path is not None:
            print(
                _safe_error(f"Intended final record: {exc.run_record_path}", user_agent)
            )
        return 1
    print(_safe_error(f"Run ID: {result.run_id}", user_agent))
    print(f"Pipeline status: {result.status}")
    if result.pipeline_result is not None:
        _print_pipeline(result.pipeline_result, user_agent)
    else:
        print("Company, metadata and dbt stages: unavailable")
    print(_safe_error(f"Final record: {result.run_record_path}", user_agent))
    if result.error_message is not None:
        print(
            _safe_error(
                f"FAILED ({result.error_stage}): {result.error_message}", user_agent
            ),
            file=sys.stderr,
        )
    return {"COMPLETE": 0, "PARTIAL": 2, "FAILED": 1}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
