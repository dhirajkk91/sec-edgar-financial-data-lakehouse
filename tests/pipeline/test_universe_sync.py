"""The nightly sync, with the SEC listing and the company stages faked.

The fakes stand in for discovery, the company run and the metadata loaders, so
these cases check the sync's own decisions: which filings are pending, when a
company counts as backfilled, what is retried, when Gold builds and what stops
the run.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import sec_edgar_lakehouse.pipeline.universe_sync as sync
from sec_edgar_lakehouse import FilingReference
from sec_edgar_lakehouse.bronze.company_filings import (
    CompanyFiling,
    CompanyFilingsDiscovery,
    CompanyFilingsDiscoveryError,
)

CIK = "0000320193"
ISSUER = sync.Issuer(CIK, "Apple Inc.", ("AAPL",))
SINCE = date(2021, 1, 1)


def filing(accession: str, form: str, filed: date) -> CompanyFiling:
    return CompanyFiling(FilingReference(CIK, accession), form, filed, filed, "x.htm")


LISTING = (
    filing("0000320193-20-000096", "10-K", date(2020, 10, 30)),  # before --since
    filing("0000320193-21-000010", "10-Q", date(2021, 1, 28)),
    filing("0000320193-21-000105", "10-K", date(2021, 10, 29)),
    filing("0000320193-22-000007", "10-K/A", date(2022, 1, 5)),  # amendment
    filing("0000320193-22-000006", "8-K", date(2022, 1, 4)),
)


def discovery(filings: tuple[CompanyFiling, ...] = LISTING) -> CompanyFilingsDiscovery:
    return CompanyFilingsDiscovery(
        cik=CIK,
        entity_name="Apple Inc.",
        tickers=("AAPL",),
        exchanges=("Nasdaq",),
        recent_filings=filings,
        submissions_url="https://data.sec.gov/submissions/CIK0000320193.json",
        retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
        submissions_content=b"{}",
    )


class Fakes:
    """Records each stage call and answers with configurable outcomes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.listing = discovery()
        self.discover_calls: list[dict[str, Any]] = []
        self.runs: list[list[str]] = []
        self.outcomes: dict[str, str] = {}
        self.fiscal_failures: set[str] = set()
        self.builds = 0
        self.terminal = False
        monkeypatch.setattr(sync, "discover_company_filings", self.discover)
        monkeypatch.setattr(sync, "execute_company_pipeline_run", self.run)
        monkeypatch.setattr(sync, "load_company_run_metadata", self.metadata)

    def discover(self, cik: str, **kwargs: Any) -> CompanyFilingsDiscovery:
        self.discover_calls.append(kwargs)
        return self.listing

    def run(self, cik: str, **kwargs: Any) -> Any:
        selected = list(kwargs["selected_accessions"])
        self.runs.append(selected)
        results = tuple(
            SimpleNamespace(
                filing=filing(accession, "10-Q", date(2021, 1, 1)),
                outcome=self.outcomes.get(accession, "COMPLETE"),
                error_message=None,
            )
            for accession in selected
        )
        pipeline = SimpleNamespace(processing=SimpleNamespace(filing_results=results))
        return SimpleNamespace(
            pipeline_result=pipeline,
            terminal_sec_block=self.terminal,
            error_message=None,
            selected=selected,
        )

    def metadata(self, company_run: Any, *, database_path: Path) -> Any:
        fiscal = tuple(
            SimpleNamespace(
                reference=FilingReference(CIK, accession),
                outcome="FAILED" if accession in self.fiscal_failures else "INSERTED",
                error_message="no DEI period"
                if accession in self.fiscal_failures
                else None,
            )
            for accession in company_run.selected
        )
        return SimpleNamespace(status="COMPLETE", fiscal_results=fiscal)

    def build(self, root: Path, **kwargs: Any) -> dict[str, Any]:
        self.builds += 1
        return {"status": "COMPLETE"}


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    return Fakes(monkeypatch)


def run(tmp_path: Path, fakes: Fakes, **kwargs: Any) -> dict[str, Any]:
    return sync.run_sync(
        [ISSUER],
        root=tmp_path,
        user_agent="Tests contact@example.org",
        since=SINCE,
        builder=fakes.build,
        **kwargs,
    )


def test_first_run_backfills_original_reports_and_builds_gold_once(
    tmp_path: Path, fakes: Fakes
) -> None:
    summary = run(tmp_path, fakes)

    assert fakes.discover_calls[0]["include_history"] is True
    assert fakes.discover_calls[0]["history_since"] == SINCE
    # Only original 10-K/10-Q filed on or after --since.
    assert fakes.runs == [["0000320193-21-000010", "0000320193-21-000105"]]
    assert fakes.builds == 1
    assert summary["status"] == "COMPLETE"
    assert summary["totals"]["new_done"] == 2
    state = json.loads((tmp_path / sync.STATE_FILE).read_text())
    assert state["companies"][CIK]["history_loaded"] is True
    latest = json.loads((tmp_path / "sync-runs/latest.json").read_text())
    assert latest == summary


def test_next_night_reads_only_recent_and_skips_gold_when_nothing_is_new(
    tmp_path: Path, fakes: Fakes
) -> None:
    run(tmp_path, fakes)
    summary = run(tmp_path, fakes)

    assert fakes.discover_calls[1]["include_history"] is False
    assert len(fakes.runs) == 1 and fakes.builds == 1
    assert summary["gold"] == {"status": "SKIPPED", "reason": "no new filings"}
    assert summary["status"] == "COMPLETE"


def test_a_new_filing_is_the_only_one_processed(tmp_path: Path, fakes: Fakes) -> None:
    run(tmp_path, fakes)
    fakes.listing = discovery(
        (*LISTING, filing("0000320193-26-000013", "10-Q", date(2026, 10, 31)))
    )
    run(tmp_path, fakes)

    assert fakes.runs[-1] == ["0000320193-26-000013"] and fakes.builds == 2


def test_failed_filings_and_missing_fiscal_periods_retry_next_night(
    tmp_path: Path, fakes: Fakes
) -> None:
    fakes.outcomes = {"0000320193-21-000010": "FAILED"}
    fakes.fiscal_failures = {"0000320193-21-000105"}
    summary = run(tmp_path, fakes)

    assert summary["status"] == "PARTIAL" and summary["totals"]["failed"] == 2
    entry = json.loads((tmp_path / sync.STATE_FILE).read_text())["companies"][CIK]
    assert entry["done"] == []
    assert entry["last_errors"] == {"0000320193-21-000105": "no DEI period"}

    # Until everything in range is done, the older listing files are read again,
    # so a failed old filing can't fall out of the recent listing.
    assert entry["history_loaded"] is False

    fakes.outcomes, fakes.fiscal_failures = {}, set()
    assert run(tmp_path, fakes)["status"] == "COMPLETE"
    assert fakes.discover_calls[-1]["include_history"] is True
    assert fakes.runs[-1] == ["0000320193-21-000010", "0000320193-21-000105"]


def test_a_filing_that_keeps_failing_is_set_aside(tmp_path: Path, fakes: Fakes) -> None:
    fakes.outcomes = {"0000320193-21-000010": "FAILED"}
    for _ in range(sync.MAX_ATTEMPTS):
        run(tmp_path, fakes)
    summary = run(tmp_path, fakes)

    # The sixth night doesn't try it again; it's reported instead.
    assert len(fakes.runs) == sync.MAX_ATTEMPTS
    assert summary["status"] == "COMPLETE"
    assert summary["totals"]["set_aside"] == 1
    assert summary["set_aside"] == {CIK: ["0000320193-21-000010"]}


def test_sec_block_stops_the_run_and_skips_gold(tmp_path: Path, fakes: Fakes) -> None:
    fakes.terminal = True
    second = sync.Issuer("0000789019", "Microsoft", ("MSFT",))
    summary = sync.run_sync(
        [ISSUER, second],
        root=tmp_path,
        user_agent="Tests contact@example.org",
        since=SINCE,
        builder=fakes.build,
    )

    assert summary["status"] == "FAILED" and summary["stopped"]
    assert len(fakes.discover_calls) == 1 and fakes.builds == 0


def test_one_company_error_is_recorded_and_the_run_continues(
    tmp_path: Path, fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def discover(cik: str, **kwargs: Any) -> CompanyFilingsDiscovery:
        if cik == "0000000001":
            raise CompanyFilingsDiscoveryError("SEC returned 404")
        return fakes.listing

    monkeypatch.setattr(sync, "discover_company_filings", discover)
    broken = sync.Issuer("0000000001", "Gone Corp", ("GONE",))
    summary = sync.run_sync(
        [broken, ISSUER],
        root=tmp_path,
        user_agent="Tests contact@example.org",
        since=SINCE,
        builder=fakes.build,
    )

    assert summary["status"] == "PARTIAL" and summary["totals"]["errors"] == 1
    assert fakes.builds == 1
    state = json.loads((tmp_path / sync.STATE_FILE).read_text())
    assert state["companies"]["0000000001"]["error"] == "SEC returned 404"


def test_dry_run_lists_pending_work_and_writes_nothing(
    tmp_path: Path, fakes: Fakes
) -> None:
    summary = run(tmp_path, fakes, dry_run=True)

    assert fakes.runs == [] and fakes.builds == 0
    assert summary["gold"]["reason"] == "dry run"
    assert list(tmp_path.iterdir()) == []


def test_ticker_mismatch_is_a_warning_not_a_failure(
    tmp_path: Path, fakes: Fakes
) -> None:
    summary = sync.run_sync(
        [sync.Issuer(CIK, "Apple Inc.", ("APPL",))],
        root=tmp_path,
        user_agent="Tests contact@example.org",
        since=SINCE,
        builder=fakes.build,
    )

    assert summary["status"] == "COMPLETE"
    assert "differ from the SEC listing" in summary["warnings"][0]


def test_universe_file_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "u.json"
    path.write_text(json.dumps({"schema_version": "1", "issuers": [{"cik": "123"}]}))
    with pytest.raises(ValueError, match="Invalid CIK"):
        sync.load_universe(path)

    repository = Path(__file__).resolve().parents[2]
    meta, issuers = sync.load_universe(repository / "config/universe-sp500.json")
    assert meta["name"] == "sp500" and len(issuers) == 500
    assert sync.default_since(date(2026, 11, 2)) == date(2021, 1, 1)


def test_cli_needs_a_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    code = sync.main(["--universe", "u.json", "--data-root", str(tmp_path)])

    assert code == 1 and "SEC_USER_AGENT" in capsys.readouterr().err


ISSUERS = [
    ISSUER,
    sync.Issuer("0000789019", "Microsoft", ("MSFT",)),
    sync.Issuer("0001045810", "NVIDIA", ("NVDA",)),
]


def test_publish_every_n_companies_during_a_long_run(
    tmp_path: Path, fakes: Fakes
) -> None:
    published: list[int] = []

    def publish() -> dict[str, object]:
        published.append(fakes.builds)
        return {}

    summary = sync.run_sync(
        ISSUERS,
        root=tmp_path,
        user_agent="Tests contact@example.org",
        since=SINCE,
        builder=fakes.build,
        publish_every=2,
        publisher=publish,
    )

    # One checkpoint after the second company, then the final build for the third.
    assert published == [1]
    assert fakes.builds == 2
    assert summary["checkpoints"] == [
        {"gold_status": "COMPLETE", "publish_status": "COMPLETE", "after_companies": 2}
    ]
    assert summary["gold"]["status"] == "COMPLETE"


def test_no_second_build_when_the_last_checkpoint_covered_everything(
    tmp_path: Path, fakes: Fakes
) -> None:
    summary = sync.run_sync(
        ISSUERS[:2],
        root=tmp_path,
        user_agent="Tests contact@example.org",
        since=SINCE,
        builder=fakes.build,
        publish_every=2,
        publisher=dict,
    )

    assert fakes.builds == 1
    assert summary["gold"] == {
        "status": "COMPLETE",
        "reason": "built at the last checkpoint",
    }


def test_a_failed_publish_is_recorded_and_the_run_goes_on(
    tmp_path: Path, fakes: Fakes
) -> None:
    def broken() -> dict[str, object]:
        raise RuntimeError("S3 unreachable")

    summary = sync.run_sync(
        ISSUERS,
        root=tmp_path,
        user_agent="Tests contact@example.org",
        since=SINCE,
        builder=fakes.build,
        publish_every=1,
        publisher=broken,
    )

    assert summary["totals"]["companies"] == 3
    assert [c["publish_status"] for c in summary["checkpoints"]] == ["FAILED"] * 3
    assert summary["checkpoints"][0]["error"] == "S3 unreachable"


def test_publish_every_needs_a_lake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "Tests contact@example.org")
    monkeypatch.delenv("SEC_EDGAR_LAKE", raising=False)

    with pytest.raises(SystemExit):
        sync.main(
            [
                "--universe",
                "u.json",
                "--data-root",
                str(tmp_path),
                "--publish-every",
                "5",
            ]
        )
