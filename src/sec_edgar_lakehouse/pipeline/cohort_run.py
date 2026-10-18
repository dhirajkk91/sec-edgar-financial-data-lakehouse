"""Plan and run a fixed local issuer cohort through the existing company APIs.

The plan freezes accession choices before ingestion. Each company keeps its own
run evidence; the shared catalog is refreshed for metadata, then Gold is built
once after the batch. This command is intentionally single-process and local.
"""

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from sec_edgar_lakehouse.bronze.company_filings import (
    CompanyFiling,
    CompanyFilingsDiscovery,
    CompanyFilingsDiscoveryError,
    discover_company_filings,
)
from sec_edgar_lakehouse.bronze.filing_request import RequestPacer
from sec_edgar_lakehouse.filing_reference import _normalize_cik
from sec_edgar_lakehouse.metadata.company_metadata import (
    CompanyMetadataLoadError,
    load_company_run_metadata,
)
from sec_edgar_lakehouse.pipeline.company_run import (
    CompanyRunError,
    _safe_error,
    execute_company_pipeline_run,
)
from sec_edgar_lakehouse.pipeline.dbt_build import DbtBuildError, run_dbt_build

_ROOT = Path(__file__).resolve().parents[3]


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    """Replace only a checkpoint file, leaving the previous one intact on write failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def _manifest(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_bytes()
    value = json.loads(content)
    if not isinstance(value, dict) or value.get("schema_version") != "1":
        raise ValueError("Cohort manifest needs schema version 1")
    issuers = value.get("issuers")
    if not isinstance(issuers, list) or len(issuers) != 20:
        raise ValueError("Cohort manifest must have exactly 20 issuers")
    ciks = []
    for rank, issuer in enumerate(issuers, 1):
        if not isinstance(issuer, dict) or issuer.get("rank") != rank:
            raise ValueError("Cohort ranks must be consecutive from 1 to 20")
        cik = issuer.get("cik")
        if not isinstance(cik, str) or _normalize_cik(cik) != cik:
            raise ValueError(f"Invalid CIK at rank {rank}")
        if not isinstance(issuer.get("tickers"), list) or not issuer["tickers"]:
            raise ValueError(f"Missing tickers at rank {rank}")
        ciks.append(cik)
    if len(set(ciks)) != 20:
        raise ValueError("Cohort CIKs must be distinct")
    date.fromisoformat(value["filing_cutoff"])
    return value, _sha(content)


def _filing(filing: CompanyFiling) -> dict[str, Any]:
    return {
        "accession_number": filing.reference.accession_number,
        "form": filing.form,
        "filing_date": filing.filing_date.isoformat(),
        "report_date": filing.report_date.isoformat() if filing.report_date else None,
        "primary_document": filing.primary_document,
        "source_listing": filing.source_listing_name or "submissions.json",
    }


def _selection(discovery: CompanyFilingsDiscovery, cutoff: date) -> dict[str, Any]:
    """Use report-date intervals as candidates, leaving DEI fiscal focus for reconciliation."""
    eligible = tuple(f for f in discovery.all_filings if f.filing_date <= cutoff)
    annual_by_end: dict[date, list[CompanyFiling]] = {}
    for filing in eligible:
        if (
            filing.form == "10-K"
            and filing.report_date is not None
            and filing.report_date <= cutoff
        ):
            annual_by_end.setdefault(filing.report_date, []).append(filing)
    ends = sorted(annual_by_end, reverse=True)
    years = []
    selected: dict[str, CompanyFiling] = {}
    gaps = []
    for index, end in enumerate(ends[:5]):
        annual_candidates = annual_by_end[end]
        previous = ends[index + 1] if index + 1 < len(ends) else None
        annual = annual_candidates[0] if len(annual_candidates) == 1 else None
        if annual is None:
            gaps.append(f"Ambiguous original 10-K for report date {end}")
        else:
            selected[annual.reference.accession_number] = annual
        quarters = sorted(
            (
                f
                for f in eligible
                if f.form == "10-Q"
                and f.report_date is not None
                and previous is not None
                and previous < f.report_date < end
            ),
            key=lambda f: (f.report_date, f.filing_date, f.reference.accession_number),
        )
        for filing in quarters:
            selected[filing.reference.accession_number] = filing
        if previous is None:
            gaps.append(f"No prior 10-K report date to bound quarters before {end}")
        if len(quarters) != 3 or len({f.report_date for f in quarters}) != 3:
            gaps.append(
                f"Expected three distinct original 10-Q periods before {end}; found {len(quarters)}"
            )
        years.append(
            {
                "annual_report_date": end.isoformat(),
                "previous_annual_report_date": previous.isoformat()
                if previous
                else None,
                "annual_candidates": [_filing(f) for f in annual_candidates],
                "annual_accession": annual.reference.accession_number
                if annual
                else None,
                "quarter_candidates": [_filing(f) for f in quarters],
                "quarter_accessions": [f.reference.accession_number for f in quarters],
                "fiscal_focus": "pending verified DEI metadata",
            }
        )
    if len(years) < 5:
        gaps.append(
            f"Only {len(years)} completed original 10-K report periods were found"
        )
    amendments = [_filing(f) for f in eligible if f.form in {"10-K/A", "10-Q/A"}]
    return {
        "candidate_filing_date_from": min(
            (f.filing_date for f in eligible), default=cutoff
        ).isoformat(),
        "candidate_filing_date_to": max(
            (f.filing_date for f in eligible), default=cutoff
        ).isoformat(),
        "years": years,
        "selected_accessions": [
            f.reference.accession_number
            for f in sorted(
                selected.values(),
                key=lambda f: (f.filing_date, f.reference.accession_number),
                reverse=True,
            )
        ],
        "selected_filings": [_filing(f) for f in selected.values()],
        "amendments_excluded": amendments,
        "plan_gaps": gaps,
    }


def _save_source(root: Path, discovery: CompanyFilingsDiscovery) -> dict[str, Any]:
    directory = root / f"cik={discovery.cik}"
    directory.mkdir(parents=True, exist_ok=False)
    main = directory / "submissions.json"
    main.write_bytes(discovery.submissions_content)
    files = [
        {
            "path": str(main.relative_to(root.parent.parent)),
            "url": discovery.submissions_url,
            "retrieved_at": discovery.retrieved_at.isoformat(),
            "sha256": _sha(discovery.submissions_content),
            "size_bytes": len(discovery.submissions_content),
        }
    ]
    for listing in discovery.history_listings:
        path = directory / listing.reference.name
        path.write_bytes(listing.content)
        files.append(
            {
                "path": str(path.relative_to(root.parent.parent)),
                "url": listing.url,
                "retrieved_at": listing.retrieved_at.isoformat(),
                "sha256": _sha(listing.content),
                "size_bytes": len(listing.content),
            }
        )
    return {"files": files}


def _plan(manifest_path: Path, root: Path, user_agent: str) -> dict[str, Any]:
    manifest, manifest_sha = _manifest(manifest_path)
    plan_path = root / "cohort-plan.json"
    if plan_path.exists():
        raise ValueError(f"Plan already exists: {plan_path}")
    source_root = root / "plan-sources" / uuid4().hex
    source_root.mkdir(parents=True)
    cutoff = date.fromisoformat(manifest["filing_cutoff"])
    pacer = RequestPacer(0.5)
    companies = []
    with httpx.Client() as client:
        for issuer in manifest["issuers"]:
            discovery = discover_company_filings(
                issuer["cik"],
                user_agent=user_agent,
                client=client,
                _pacer=pacer,
            )
            selection = _selection(discovery, cutoff)
            history_since = None
            if selection["plan_gaps"]:
                history_since = date(cutoff.year - 7, 1, 1)
                discovery = discover_company_filings(
                    issuer["cik"],
                    user_agent=user_agent,
                    client=client,
                    _pacer=pacer,
                    include_history=True,
                    history_since=history_since,
                )
                selection = _selection(discovery, cutoff)
            listed = {ticker.upper().replace(".", "-") for ticker in discovery.tickers}
            expected = {
                ticker.upper().replace(".", "-") for ticker in issuer["tickers"]
            }
            if listed and not expected <= listed:
                raise ValueError(
                    f"SEC ticker identity differs for rank {issuer['rank']} CIK {issuer['cik']}"
                )
            source = _save_source(source_root, discovery)
            source_warnings = [
                {
                    "listing": listing.reference.name,
                    "range_mismatch_count": len(listing.range_mismatches),
                    "sample_accessions": list(listing.range_mismatches[:3]),
                }
                for listing in discovery.history_listings
                if listing.range_mismatches
            ]
            if not listed:
                source_warnings.append(
                    {
                        "cik": issuer["cik"],
                        "warning": "SEC submissions ticker list is empty; CIK and entity name verified",
                    }
                )
            selected_mismatches = {
                accession
                for listing in discovery.history_listings
                for accession in listing.range_mismatches
                if accession in selection["selected_accessions"]
            }
            selection["plan_gaps"].extend(
                f"Selected filing differs from history manifest date range: {accession}"
                for accession in sorted(selected_mismatches)
            )
            companies.append(
                {
                    **issuer,
                    "sec_entity_name": discovery.entity_name,
                    **selection,
                    "history_since": history_since.isoformat()
                    if history_since
                    else None,
                    "source_warnings": source_warnings,
                    "source_evidence": source,
                }
            )
            print(
                f"Plan {issuer['rank']:2}/20 {issuer['name']}: "
                f"{len(selection['years'])} annual periods, "
                f"{len(selection['selected_accessions'])} accessions, "
                f"{len(selection['plan_gaps'])} gaps",
                flush=True,
            )
    plan = {
        "schema_version": "1",
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha,
        "selection_as_of": manifest["selection_as_of"],
        "filing_cutoff": manifest["filing_cutoff"],
        "data_root": str(root),
        "companies": companies,
        "estimated_selected_filings": sum(
            len(c["selected_accessions"]) for c in companies
        ),
        "plan_gap_count": sum(len(c["plan_gaps"]) for c in companies),
    }
    _write_json(plan_path, plan)
    return plan


def _read_plan(manifest_path: Path, root: Path) -> tuple[dict[str, Any], str]:
    plan_path = root / "cohort-plan.json"
    content = plan_path.read_bytes()
    plan = json.loads(content)
    _, manifest_sha = _manifest(manifest_path)
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != "1"
        or plan.get("manifest_sha256") != manifest_sha
        or plan.get("data_root") != str(root)
        or len(plan.get("companies", [])) != 20
    ):
        raise ValueError("Saved plan does not match this manifest and data root")
    for company in plan["companies"]:
        for source in company["source_evidence"]["files"]:
            path = root / source["path"]
            if (
                path.is_symlink()
                or not path.is_file()
                or _sha(path.read_bytes()) != source["sha256"]
            ):
                raise ValueError(f"Plan source evidence changed: {path}")
    return plan, _sha(content)


def _company_attempt(
    company: dict[str, Any], root: Path, user_agent: str, cutoff: date
) -> dict[str, Any]:
    run_id = f"cohort_{uuid4().hex}"
    selected = tuple(company["selected_accessions"])
    if not selected:
        return {
            "run_id": run_id,
            "status": "FAILED",
            "reason": "Plan has no selected accessions",
            "filings": {},
        }
    company_run = execute_company_pipeline_run(
        company["cik"],
        user_agent=user_agent,
        forms=("10-K", "10-Q"),
        bronze_directory=root / "bronze",
        silver_directory=root / "silver",
        database_path=root / "query" / "sec_edgar.duckdb",
        processing_run_id=run_id,
        run_directory=root / "runs",
        filed_on_or_before=cutoff,
        include_history=any(
            filing["source_listing"] != "submissions.json"
            for filing in company["selected_filings"]
        ),
        history_since=(
            date.fromisoformat(company["history_since"])
            if company["history_since"]
            else None
        ),
        selected_accessions=selected,
    )
    pipeline = company_run.pipeline_result
    filings: dict[str, dict[str, Any]] = {
        accession: {"outcome": "NOT_ATTEMPTED"} for accession in selected
    }
    if pipeline:
        for process_item in pipeline.processing.filing_results:
            filings[process_item.filing.reference.accession_number] = {
                "outcome": process_item.outcome,
                "bronze_status": process_item.bronze_status,
                "silver_status": process_item.silver_status,
                "error_stage": process_item.error_stage,
                "error_message": _safe_error(process_item.error_message, user_agent),
            }
    metadata = load_company_run_metadata(
        company_run, database_path=root / "query" / "sec_edgar.duckdb"
    )
    issues = list(company["plan_gaps"])
    if pipeline:
        planned = {row["accession_number"]: row for row in company["selected_filings"]}
        for process_item in pipeline.processing.filing_results:
            accession = process_item.filing.reference.accession_number
            actual = _filing(process_item.filing)
            expected = planned[accession]
            for field in (
                "form",
                "filing_date",
                "report_date",
                "primary_document",
                "source_listing",
            ):
                if actual[field] != expected[field]:
                    issues.append(f"Source listing changed {field} for {accession}")
    for fiscal_item in metadata.fiscal_results:
        entry = filings[fiscal_item.reference.accession_number]
        entry["fiscal_metadata_outcome"] = fiscal_item.outcome
        entry["fiscal_metadata_status"] = fiscal_item.status
        if fiscal_item.load_result:
            focus = fiscal_item.load_result.metadata
            entry["fiscal_year_focus"] = focus.fiscal_year_focus
            entry["fiscal_period_focus"] = focus.fiscal_period_focus
            entry["document_period_end_date"] = (
                focus.document_period_end_date.isoformat()
                if focus.document_period_end_date
                else None
            )
        if fiscal_item.error_message:
            entry["fiscal_error"] = _safe_error(fiscal_item.error_message, user_agent)
    for year in company["years"]:
        annual = year["annual_accession"]
        if annual:
            annual_focus = filings[annual].get("fiscal_year_focus")
            if (
                filings[annual].get("fiscal_period_focus") != "FY"
                or annual_focus is None
            ):
                issues.append(
                    f"Annual DEI fiscal focus missing or conflicting: {annual}"
                )
            if (
                filings[annual].get("document_period_end_date")
                != year["annual_report_date"]
            ):
                issues.append(f"Annual DEI period end differs from listing: {annual}")
            for index, accession in enumerate(year["quarter_accessions"], 1):
                q = filings[accession]
                if len(year["quarter_accessions"]) == 3 and (
                    q.get("fiscal_year_focus") != annual_focus
                    or q.get("fiscal_period_focus") != f"Q{index}"
                ):
                    issues.append(
                        f"Quarter DEI fiscal focus conflicts with annual: {accession}"
                    )
                planned_quarter = next(
                    row
                    for row in year["quarter_candidates"]
                    if row["accession_number"] == accession
                )
                if q.get("document_period_end_date") != planned_quarter["report_date"]:
                    issues.append(
                        f"Quarter DEI period end differs from listing: {accession}"
                    )
    work_complete = (
        pipeline is not None
        and pipeline.catalog_status in {"COMPLETE", "PARTIAL"}
        and metadata.status in {"COMPLETE", "PARTIAL"}
        and all(
            item["outcome"] in {"COMPLETE", "PARTIAL", "SKIPPED"}
            for item in filings.values()
        )
        and all(
            item.get("fiscal_metadata_outcome") in {"INSERTED", "ALREADY_EXISTS"}
            for item in filings.values()
        )
    )
    return {
        "run_id": run_id,
        "status": "COMPLETE"
        if work_complete and not issues
        else "PARTIAL"
        if work_complete
        else "FAILED",
        "work_complete": work_complete,
        "terminal_sec_block": company_run.terminal_sec_block,
        "company_run_status": company_run.status,
        "company_record_path": str(company_run.run_record_path),
        "company_record_sha256": _sha(company_run.run_record_path.read_bytes()),
        "catalog_status": pipeline.catalog_status if pipeline else "SKIPPED",
        "metadata_status": metadata.status,
        "filing_metadata_status": metadata.filing_metadata_result.status
        if metadata.filing_metadata_result
        else None,
        "filing_metadata_error": _safe_error(
            metadata.filing_metadata_error, user_agent
        ),
        "filings": filings,
        "coverage_issues": issues,
        "reason": _safe_error(company_run.error_message, user_agent),
    }


def _build(root: Path, timeout: float) -> dict[str, Any]:
    attempt = uuid4().hex
    directory = root / "dbt"
    directory.mkdir(exist_ok=True)
    profiles = directory / f"profiles-{attempt}"
    profiles.mkdir()
    _write_json(
        profiles / "profiles.yml",
        {
            "sec_edgar_gold": {
                "target": "dev",
                "outputs": {
                    "dev": {
                        "type": "duckdb",
                        "path": "{{ env_var('SEC_EDGAR_DUCKDB_PATH') }}",
                        "schema": "gold",
                        "threads": 1,
                        "settings": {
                            "threads": 1,
                            "memory_limit": "4GB",
                            "preserve_insertion_order": True,
                        },
                    }
                },
            }
        },
    )
    result = run_dbt_build(
        database_path=root / "query" / "sec_edgar.duckdb",
        project_directory=_ROOT / "transform",
        profiles_directory=profiles,
        artifact_directory=directory / f"artifacts-{attempt}",
        timeout_seconds=timeout,
    )
    return {
        "status": result.status,
        "artifact_directory": str(result.artifact_directory),
        "profiles_directory": str(profiles),
        "invocation_id": result.invocation_id,
        "node_status_counts": dict(result.node_status_counts),
        "error_message": result.error_message,
    }


def _execute(
    manifest_path: Path,
    root: Path,
    user_agent: str,
    *,
    resume: bool,
    max_companies: int | None,
    ranks: set[int] | None,
    rerun_complete: bool,
    build_after_batch: bool,
    dbt_timeout: float,
) -> dict[str, Any]:
    plan, plan_sha = _read_plan(manifest_path, root)
    state_path = root / "cohort-state.json"
    if resume:
        state = json.loads(state_path.read_text())
        if state.get("plan_sha256") != plan_sha:
            raise ValueError("Saved state does not match the plan")
    else:
        if state_path.exists():
            raise ValueError("State already exists; use resume")
        state = {
            "schema_version": "1",
            "plan_sha256": plan_sha,
            "status": "PARTIAL",
            "companies": {},
            "dbt_attempts": [],
        }
        _write_json(state_path, state)
    processed = 0
    for company in plan["companies"]:
        if ranks is not None and company["rank"] not in ranks:
            continue
        cik = company["cik"]
        prior = state["companies"].get(cik, {})
        if prior.get("work_complete") and not rerun_complete:
            path = Path(prior["company_record_path"])
            if (
                path.is_symlink()
                or _sha(path.read_bytes()) != prior["company_record_sha256"]
            ):
                raise ValueError(f"Completed company evidence changed: {cik}")
            continue
        if max_companies is not None and processed >= max_companies:
            break
        if processed:
            time.sleep(
                0.5
            )  # The per-company pacer is new; keep the boundary paced too.
        started = time.monotonic()
        try:
            attempt = _company_attempt(
                company, root, user_agent, date.fromisoformat(plan["filing_cutoff"])
            )
        except (CompanyRunError, CompanyMetadataLoadError, OSError, ValueError) as exc:
            attempt = {
                "run_id": None,
                "status": "FAILED",
                "work_complete": False,
                "terminal_sec_block": False,
                "reason": _safe_error(str(exc), user_agent),
                "filings": {
                    a: {"outcome": "NOT_ATTEMPTED"}
                    for a in company["selected_accessions"]
                },
            }
        attempt["elapsed_seconds"] = round(time.monotonic() - started, 2)
        attempts = prior.get("attempts", []) + [attempt]
        state["companies"][cik] = {**attempt, "attempts": attempts}
        _write_json(state_path, state)
        processed += 1
        counts: dict[str, int] = {}
        for item in attempt["filings"].values():
            counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
        print(
            f"{company['rank']:2}/20 {company['name']}: selected={len(company['selected_accessions'])} "
            f"complete={counts.get('COMPLETE', 0)} partial={counts.get('PARTIAL', 0)} "
            f"failed={counts.get('FAILED', 0)} reused={counts.get('SKIPPED', 0)} "
            f"not_attempted={counts.get('NOT_ATTEMPTED', 0)} "
            f"elapsed={attempt['elapsed_seconds']}s resume={state_path}",
            flush=True,
        )
        if attempt.get("terminal_sec_block"):
            state["terminal_sec_block"] = True
            _write_json(state_path, state)
            break
    all_work = len(state["companies"]) == 20 and all(
        entry.get("work_complete") for entry in state["companies"].values()
    )
    if build_after_batch or (all_work and processed > 0 and not rerun_complete):
        try:
            build = _build(root, dbt_timeout)
        except (DbtBuildError, OSError, ValueError) as exc:
            build = {
                "status": "FAILED",
                "error_message": _safe_error(str(exc), user_agent),
            }
        state["dbt_attempts"].append(build)
        _write_json(state_path, state)
        print(
            f"Gold build: {build['status']} artifacts={build.get('artifact_directory')}",
            flush=True,
        )
    latest_build = state["dbt_attempts"][-1] if state["dbt_attempts"] else None
    all_coverage = all_work and all(
        not entry["coverage_issues"] for entry in state["companies"].values()
    )
    state["status"] = (
        "COMPLETE"
        if all_coverage and latest_build and latest_build["status"] == "COMPLETE"
        else "PARTIAL"
    )
    _write_json(state_path, state)
    return state


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "execute", "resume"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--max-companies", type=int)
    parser.add_argument("--ranks", type=int, nargs="+")
    parser.add_argument("--rerun-complete", action="store_true")
    parser.add_argument("--build-after-batch", action="store_true")
    parser.add_argument("--dbt-timeout-seconds", type=float, default=3600)
    args = parser.parse_args(arguments)
    user_agent = os.environ.get("SEC_USER_AGENT", "")
    if not user_agent.strip():
        print("FAILED: SEC_USER_AGENT must be set and non-blank", file=sys.stderr)
        return 1
    root = args.data_root.resolve()
    if not root.is_relative_to((_ROOT / "data").resolve()):
        print(
            "FAILED: data root must be inside the ignored repository data/ directory",
            file=sys.stderr,
        )
        return 1
    if args.max_companies is not None and args.max_companies <= 0:
        parser.error("--max-companies must be positive")
    if args.ranks is not None and (
        len(args.ranks) != len(set(args.ranks))
        or any(rank < 1 or rank > 20 for rank in args.ranks)
    ):
        parser.error("--ranks must be distinct values from 1 to 20")
    if args.rerun_complete and args.mode != "resume":
        parser.error("--rerun-complete requires resume")
    if args.rerun_complete and args.ranks is None:
        parser.error("--rerun-complete requires --ranks")
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cohort.lock"
    try:
        with lock.open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.mode == "plan":
                plan = _plan(args.manifest, root, user_agent)
                print(
                    f"Plan: {root / 'cohort-plan.json'} | selected={plan['estimated_selected_filings']} | gaps={plan['plan_gap_count']}"
                )
                return 0
            state = _execute(
                args.manifest,
                root,
                user_agent,
                resume=args.mode == "resume",
                max_companies=args.max_companies,
                ranks=set(args.ranks) if args.ranks is not None else None,
                rerun_complete=args.rerun_complete,
                build_after_batch=args.build_after_batch,
                dbt_timeout=args.dbt_timeout_seconds,
            )
            print(
                f"Cohort status: {state['status']} | state={root / 'cohort-state.json'}"
            )
            return 0 if state["status"] == "COMPLETE" else 2
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        CompanyFilingsDiscoveryError,
    ) as exc:
        print(f"FAILED: {_safe_error(str(exc), user_agent)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
