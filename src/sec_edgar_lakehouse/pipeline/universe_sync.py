"""Keep a universe of companies current: process new filings, then build Gold once.

    sec-edgar sync --universe config/universe-sp500.json --data-root data/lake

For each issuer the command reads the SEC company listing, picks the original
10-K and 10-Q filings filed on or after ``--since``, and runs only the ones not
already done through Bronze, Silver, the catalog and the metadata loaders, using
the same company APIs as the cohort run. Gold is built once at the end, and only
when something new landed. The first run for a company also reads the SEC's older
listing files (the backfill); later runs read just the recent listing.

State is saved after every company, so an interrupted run picks up where it
stopped and a failed filing is simply tried again the next night. A SEC block
page or long Retry-After stops the whole run. Each run writes a summary to
``<data-root>/sync-runs/``. Exit code 0 means every filing is done, 2 means some
failed or Gold didn't build, 1 means the run couldn't start.
"""

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
from sec_edgar_lakehouse.pipeline.dbt_build import DbtBuildError, build_gold

FORMS = ("10-K", "10-Q")
# Outcomes that mean a filing needs no more work. FAILED and NOT_ATTEMPTED retry.
DONE = frozenset({"COMPLETE", "PARTIAL", "SKIPPED"})
STATE_FILE = "sync-state.json"
# A filing that fails this many nights in a row is set aside and reported, so one
# filing the parser can't read doesn't keep every night PARTIAL forever.
MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class Issuer:
    """One company in the universe file."""

    cik: str
    name: str
    tickers: tuple[str, ...]


def load_universe(path: Path) -> tuple[dict[str, Any], tuple[Issuer, ...]]:
    """Read and check the universe file: schema version 1 and distinct, valid CIKs."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "1":
        raise ValueError("Universe file needs schema_version 1")
    raw = value.get("issuers")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Universe file must list at least one issuer")
    issuers = []
    for item in raw:
        cik = item.get("cik") if isinstance(item, dict) else None
        if not isinstance(cik, str) or _normalize_cik(cik) != cik:
            raise ValueError(f"Invalid CIK in universe: {cik!r}")
        tickers = item.get("tickers")
        if not isinstance(tickers, list) or not all(
            isinstance(t, str) for t in tickers
        ):
            raise ValueError(f"Tickers must be a list of strings for {cik}")
        issuers.append(Issuer(cik, str(item.get("name") or cik), tuple(tickers)))
    if len({issuer.cik for issuer in issuers}) != len(issuers):
        raise ValueError("Universe CIKs must be distinct")
    return value, tuple(issuers)


def default_since(today: date) -> date:
    """January 1 five years back: five full fiscal years plus the current one."""
    return date(today.year - 5, 1, 1)


def candidate_filings(
    discovery: CompanyFilingsDiscovery, since: date
) -> tuple[CompanyFiling, ...]:
    """Original 10-K and 10-Q filings filed on or after ``since``, oldest first.

    Amendments are left out, as in the cohort: Gold has no amendment policy yet.
    """
    return tuple(
        sorted(
            (
                f
                for f in discovery.all_filings
                if f.form in FORMS and f.filing_date >= since
            ),
            key=lambda f: (f.filing_date, f.reference.accession_number),
        )
    )


def _write_json(path: Path, value: Any) -> None:
    """Replace a state or summary file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", delete=False, encoding="utf-8"
    ) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stream.name, path)


def _read_state(root: Path) -> dict[str, Any]:
    path = root / STATE_FILE
    if not path.exists():
        return {"schema_version": "1", "companies": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema_version") != "1":
        raise ValueError(f"Unrecognised sync state: {path}")
    return state


def _ticker_warning(issuer: Issuer, discovery: CompanyFilingsDiscovery) -> str | None:
    """Flag a universe ticker the SEC doesn't list for this CIK; the run continues."""
    listed = {t.upper().replace(".", "-") for t in discovery.tickers}
    expected = {t.upper().replace(".", "-") for t in issuer.tickers}
    if listed and not expected <= listed:
        return (
            f"{issuer.name} ({issuer.cik}): universe tickers {sorted(expected)} "
            f"differ from the SEC listing {sorted(listed)}"
        )
    return None


def sync_company(
    issuer: Issuer,
    *,
    root: Path,
    user_agent: str,
    since: date,
    entry: dict[str, Any],
    client: httpx.Client,
    pacer: RequestPacer,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Find this company's undone filings and run them; returns its updated entry."""
    backfill = not entry.get("history_loaded", False)
    discovery = discover_company_filings(
        issuer.cik,
        user_agent=user_agent,
        client=client,
        _pacer=pacer,
        include_history=backfill,
        history_since=since if backfill else None,
    )
    done = set(entry.get("done", []))
    failures: dict[str, int] = dict(entry.get("failures", {}))
    candidates = candidate_filings(discovery, since)
    set_aside = sorted(
        f.reference.accession_number
        for f in candidates
        if f.reference.accession_number not in done
        and failures.get(f.reference.accession_number, 0) >= MAX_ATTEMPTS
    )
    pending = [
        f
        for f in candidates
        if f.reference.accession_number not in done
        and failures.get(f.reference.accession_number, 0) < MAX_ATTEMPTS
    ]
    result: dict[str, Any] = {
        **entry,
        "name": issuer.name,
        "checked_at": datetime.now(UTC).isoformat(),
        "candidates": len(candidates),
        "pending": [f.reference.accession_number for f in pending],
        "set_aside": set_aside,
        "warning": _ticker_warning(issuer, discovery),
    }
    if dry_run or not pending:
        # With nothing left to do, the older listing files aren't needed again.
        result["history_loaded"] = entry.get("history_loaded", False) or not dry_run
        result["last_outcomes"] = {}
        return result

    run_id = f"sync_{uuid4().hex}"
    database = root / "query" / "sec_edgar.duckdb"
    company_run = execute_company_pipeline_run(
        issuer.cik,
        user_agent=user_agent,
        forms=FORMS,
        bronze_directory=root / "bronze",
        silver_directory=root / "silver",
        database_path=database,
        processing_run_id=run_id,
        run_directory=root / "runs",
        filed_on_or_after=since,
        client=client,
        include_history=backfill,
        history_since=since if backfill else None,
        selected_accessions=[f.reference.accession_number for f in pending],
    )
    outcomes = {f.reference.accession_number: "NOT_ATTEMPTED" for f in pending}
    errors: dict[str, str] = {}
    pipeline = company_run.pipeline_result
    if pipeline is not None:
        for item in pipeline.processing.filing_results:
            accession = item.filing.reference.accession_number
            outcomes[accession] = item.outcome
            if item.error_message:
                errors[accession] = str(_safe_error(item.error_message, user_agent))
    metadata = load_company_run_metadata(company_run, database_path=database)
    for fiscal in metadata.fiscal_results:
        accession = fiscal.reference.accession_number
        # A filing counts as done only when its fiscal period is known too.
        if (
            fiscal.outcome not in {"INSERTED", "ALREADY_EXISTS"}
            and outcomes.get(accession) in DONE
        ):
            outcomes[accession] = "FAILED"
            errors[accession] = str(
                _safe_error(
                    fiscal.error_message or "fiscal metadata missing", user_agent
                )
            )
    newly_done = {a for a, outcome in outcomes.items() if outcome in DONE}
    for accession, outcome in outcomes.items():
        if outcome in DONE:
            failures.pop(accession, None)
        else:
            failures[accession] = failures.get(accession, 0) + 1
    result.update(
        {
            "done": sorted(done | newly_done),
            "failures": failures,
            # Keep reading the older listing files until every filing in range is
            # done, or a failed old filing would drop out of the recent listing.
            "history_loaded": not (set(outcomes) - newly_done),
            "last_run_id": run_id,
            "last_outcomes": outcomes,
            "last_errors": errors,
            "metadata_status": metadata.status,
            "terminal_sec_block": company_run.terminal_sec_block,
            "reason": _safe_error(company_run.error_message, user_agent),
        }
    )
    return result


def run_sync(
    issuers: Sequence[Issuer],
    *,
    root: Path,
    user_agent: str,
    since: date,
    build: bool = True,
    dry_run: bool = False,
    dbt_timeout: float = 3600.0,
    client_factory: Callable[[], httpx.Client] = httpx.Client,
    builder: Callable[..., dict[str, Any]] = build_gold,
) -> dict[str, Any]:
    """Sync every issuer in order, saving state after each one, then build Gold."""
    started = datetime.now(UTC)
    state = _read_state(root)
    companies: dict[str, Any] = state["companies"]
    pacer = RequestPacer(0.5)
    totals = {"companies": 0, "new_done": 0, "failed": 0, "errors": 0, "set_aside": 0}
    warnings: list[str] = []
    stopped: str | None = None
    with client_factory() as client:
        for issuer in issuers:
            entry = companies.get(issuer.cik, {})
            before = set(entry.get("done", []))
            try:
                updated = sync_company(
                    issuer,
                    root=root,
                    user_agent=user_agent,
                    since=since,
                    entry=entry,
                    client=client,
                    pacer=pacer,
                    dry_run=dry_run,
                )
            except (
                CompanyFilingsDiscoveryError,
                CompanyRunError,
                CompanyMetadataLoadError,
                OSError,
                ValueError,
            ) as exc:
                updated = {
                    **entry,
                    "name": issuer.name,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "error": _safe_error(str(exc), user_agent),
                }
                totals["errors"] += 1
                if isinstance(exc, CompanyFilingsDiscoveryError) and exc.stop_run:
                    stopped = f"SEC stop condition at {issuer.cik}: {updated['error']}"
            else:
                updated.pop("error", None)
            companies[issuer.cik] = updated
            totals["companies"] += 1
            totals["new_done"] += len(set(updated.get("done", [])) - before)
            totals["failed"] += sum(
                1 for o in updated.get("last_outcomes", {}).values() if o not in DONE
            )
            totals["set_aside"] += len(updated.get("set_aside", []))
            if updated.get("warning"):
                warnings.append(str(updated["warning"]))
            if updated.get("terminal_sec_block"):
                stopped = f"SEC block while processing {issuer.cik}"
            if not dry_run:
                _write_json(root / STATE_FILE, state)
            print(
                f"{totals['companies']:3}/{len(issuers)} {issuer.name}: "
                f"pending={len(updated.get('pending', []))} "
                f"done_now={len(set(updated.get('done', [])) - before)}"
                + (f" error={updated['error']}" if updated.get("error") else ""),
                flush=True,
            )
            if stopped:
                break
    gold: dict[str, Any] = {"status": "SKIPPED", "reason": "no new filings"}
    if dry_run:
        gold = {"status": "SKIPPED", "reason": "dry run"}
    elif stopped:
        gold = {"status": "SKIPPED", "reason": "run stopped early"}
    elif build and totals["new_done"]:
        try:
            gold = builder(root, timeout_seconds=dbt_timeout)
        except (DbtBuildError, OSError, ValueError) as exc:
            gold = {
                "status": "FAILED",
                "error_message": _safe_error(str(exc), user_agent),
            }
    status = (
        "FAILED"
        if stopped or gold["status"] == "FAILED"
        else "PARTIAL"
        if totals["failed"] or totals["errors"]
        else "COMPLETE"
    )
    summary = {
        "schema_version": "1",
        "status": status,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "since": since.isoformat(),
        "dry_run": dry_run,
        "totals": totals,
        "gold": gold,
        "stopped": stopped,
        "warnings": warnings,
        "set_aside": {
            cik: entry["set_aside"]
            for cik, entry in companies.items()
            if entry.get("set_aside")
        },
    }
    if not dry_run:
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        _write_json(root / "sync-runs" / f"{stamp}.json", summary)
        _write_json(root / "sync-runs" / "latest.json", summary)
    return summary


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        help="Earliest filing date to keep (default: January 1, five years back)",
    )
    parser.add_argument("--ciks", nargs="+", help="Only these CIKs from the universe")
    parser.add_argument("--max-companies", type=int, help="Stop after this many")
    parser.add_argument("--no-build", action="store_true", help="Skip the Gold build")
    parser.add_argument(
        "--dry-run", action="store_true", help="List pending filings; change nothing"
    )
    parser.add_argument("--dbt-timeout-seconds", type=float, default=3600)
    args = parser.parse_args(arguments)
    user_agent = os.environ.get("SEC_USER_AGENT", "")
    if not user_agent.strip():
        print("FAILED: SEC_USER_AGENT must be set and non-blank", file=sys.stderr)
        return 1
    if args.max_companies is not None and args.max_companies <= 0:
        parser.error("--max-companies must be positive")
    try:
        _, issuers = load_universe(args.universe)
        if args.ciks:
            wanted = {_normalize_cik(cik) for cik in args.ciks}
            issuers = tuple(i for i in issuers if i.cik in wanted)
            if len(issuers) != len(wanted):
                raise ValueError("Some --ciks are not in the universe file")
        if args.max_companies:
            issuers = issuers[: args.max_companies]
        root = args.data_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        since = args.since or default_since(datetime.now(UTC).date())
        with (root / ".sync.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"FAILED: another sync holds {root}", file=sys.stderr)
                return 1
            started = time.monotonic()
            summary = run_sync(
                issuers,
                root=root,
                user_agent=user_agent,
                since=since,
                build=not args.no_build,
                dry_run=args.dry_run,
                dbt_timeout=args.dbt_timeout_seconds,
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        CompanyFilingsDiscoveryError,
    ) as exc:
        print(f"FAILED: {_safe_error(str(exc), user_agent)}", file=sys.stderr)
        return 1
    totals = summary["totals"]
    print(
        f"Sync {summary['status']}: {totals['companies']} companies, "
        f"{totals['new_done']} new filings done, {totals['failed']} failed, "
        f"{totals['errors']} company errors, Gold {summary['gold']['status']} "
        f"in {time.monotonic() - started:.0f}s"
    )
    return 0 if summary["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
