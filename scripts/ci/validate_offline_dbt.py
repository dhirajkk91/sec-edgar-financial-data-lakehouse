"""Build Gold once from fictional filings, then check values, warnings and lineage."""

import argparse
import os
import subprocess
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from synthetic_catalog import ACCESSIONS, CIK, build_synthetic_catalog, write_json

from sec_edgar_lakehouse import run_dbt_build

_DUCKDB_SETTINGS = {
    "threads": 1,
    "memory_limit": "2GB",
    "preserve_insertion_order": True,
}
_RESOURCE_BUDGET = {"dbt_concurrency": 1, "duckdb_settings": _DUCKDB_SETTINGS}


def records(connection: duckdb.DuckDBPyConnection, query: str) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def verify_gold(database: Path) -> dict[str, Any]:
    """Check fixed fixture expectations and resolve every financial input back to Silver."""
    usd = "{http://www.xbrl.org/2003/iso4217}USD"
    eps_unit = f"({usd}) / ({{http://www.xbrl.org/2003/instance}}shares)"
    with duckdb.connect(
        str(database), read_only=True, config=_DUCKDB_SETTINGS
    ) as connection:
        resources = {
            "configured_budget": _RESOURCE_BUDGET,
            "profiles_path": str(database.parent / "profiles/profiles.yml"),
            "observed_read_only_settings": records(
                connection,
                "SELECT current_setting('threads') AS threads, "
                "current_setting('memory_limit') AS memory_limit, "
                "current_setting('preserve_insertion_order') AS preserve_insertion_order",
            )[0],
        }
        write_json(database.parent / "resource-summary.json", resources)
        metrics = records(
            connection,
            "SELECT * FROM gold.fct_financial_metrics ORDER BY cik, accession_number, metric_code, period_start, period_instant",
        )
        filings = records(
            connection, "SELECT * FROM gold.dim_filings ORDER BY cik, accession_number"
        )
        issues = records(
            connection,
            "SELECT * FROM gold.metric_quality_issues ORDER BY cik, accession_number, metric_code, issue_code",
        )
        facts = {
            row["source_occurrence_id"]: row
            for row in records(connection, "SELECT * FROM silver.facts")
        }
        assert len(metrics) == 33 and len(filings) == 3 and len(issues) == 3
        for row in metrics:
            assert row["cik"] == CIK and isinstance(row["value_decimal"], Decimal)
            assert row["unit_expression"] == (
                eps_unit if row["metric_code"] == "diluted_eps" else usd
            )
            for prefix, accession in (
                ("current", row["accession_number"]),
                ("previous", row["previous_accession_number"]),
            ):
                ids = row[f"{prefix}_supporting_occurrence_ids"]
                if accession is None:
                    assert ids == [] and row[f"{prefix}_source_occurrence_id"] is None
                    continue
                assert ids and len(ids) == len(set(ids))
                inputs = [facts[identity] for identity in ids]
                for source in inputs:
                    assert (
                        source["cik"],
                        source["accession_number"],
                        source["source_document_name"],
                        source["source_sha256"],
                        source["value_decimal"],
                    ) == (
                        CIK,
                        accession,
                        row[f"{prefix}_source_document_name"],
                        row[f"{prefix}_source_sha256"],
                        row[f"{prefix}_value_decimal"],
                    )
                # These duplicates share concept priority; document order must survive.
                if row["metric_code"] == "diluted_eps":
                    assert ids == [
                        source["source_occurrence_id"]
                        for source in sorted(
                            inputs, key=lambda source: source["source_ordinal"]
                        )
                    ]
                    assert len(ids) == 2
                if row["metric_code"] == "revenue":
                    assert [source["concept_local_name"] for source in inputs] == [
                        "RevenueFromContractWithCustomerExcludingAssessedTax",
                        "Revenues",
                    ]
                assert row[f"{prefix}_source_occurrence_id"] == ids[0]
            if row["value_origin"] == "reported":
                assert row["value_decimal"] == row["current_value_decimal"]
                assert row["derivation_method"] is None
        for accession, value, label in zip(
            ACCESSIONS,
            ("1.230000000000000001", "2.340000000000000001", "5.670000000000000001"),
            ("quarter", "quarter", "annual"),
            strict=True,
        ):
            eps = [
                row
                for row in metrics
                if row["accession_number"] == accession
                and row["metric_code"] == "diluted_eps"
            ]
            assert len(eps) == 1 and eps[0]["value_decimal"] == Decimal(value)
            assert (
                eps[0]["period_label"] == label and eps[0]["value_origin"] == "reported"
            )
        derived = [row for row in metrics if row["value_origin"] == "derived"]
        for accession, value, instant in zip(
            ACCESSIONS,
            ("1000", "1200", "1400"),
            ("2025-06-30", "2025-09-30", "2025-12-31"),
            strict=True,
        ):
            assets = [
                row
                for row in metrics
                if row["accession_number"] == accession
                and row["metric_code"] == "total_assets"
            ]
            assert len(assets) == 1 and assets[0]["value_decimal"] == Decimal(value)
            assert assets[0]["period_label"] == "instant"
            assert str(assets[0]["period_instant"]) == instant
        expected = {
            (ACCESSIONS[1], "operating_cash_flow"): (
                "75",
                "175",
                "100",
                ACCESSIONS[0],
                "2025-07-01",
                "2025-09-30",
                "CURRENT_YTD_MINUS_PREVIOUS_CUMULATIVE",
            ),
            (ACCESSIONS[1], "capital_expenditures"): (
                "15",
                "35",
                "20",
                ACCESSIONS[0],
                "2025-07-01",
                "2025-09-30",
                "CURRENT_YTD_MINUS_PREVIOUS_CUMULATIVE",
            ),
            (ACCESSIONS[2], "revenue"): (
                "450",
                "1200",
                "750",
                ACCESSIONS[1],
                "2025-10-01",
                "2025-12-31",
                "ANNUAL_MINUS_Q3_YTD",
            ),
            (ACCESSIONS[2], "net_income"): (
                "45",
                "120",
                "75",
                ACCESSIONS[1],
                "2025-10-01",
                "2025-12-31",
                "ANNUAL_MINUS_Q3_YTD",
            ),
            (ACCESSIONS[2], "operating_cash_flow"): (
                "85",
                "260",
                "175",
                ACCESSIONS[1],
                "2025-10-01",
                "2025-12-31",
                "ANNUAL_MINUS_Q3_YTD",
            ),
            (ACCESSIONS[2], "capital_expenditures"): (
                "25",
                "60",
                "35",
                ACCESSIONS[1],
                "2025-10-01",
                "2025-12-31",
                "ANNUAL_MINUS_Q3_YTD",
            ),
        }
        assert len(derived) == len(expected)
        for row in derived:
            value, current, previous, accession, start, end, method = expected[
                (row["accession_number"], row["metric_code"])
            ]
            assert (
                row["value_decimal"],
                row["current_value_decimal"],
                row["previous_value_decimal"],
                row["previous_accession_number"],
                str(row["period_start"]),
                str(row["period_end"]),
                row["derivation_method"],
            ) == (
                Decimal(value),
                Decimal(current),
                Decimal(previous),
                accession,
                start,
                end,
                method,
            )
            assert (
                row["period_label"] == "derived_quarter"
                and row["previous_supporting_occurrence_ids"]
            )
        assert {
            (
                row["accession_number"],
                row["metric_code"],
                row["issue_code"],
                row["severity"],
            )
            for row in issues
        } == {
            (ACCESSIONS[0], "cash_and_equivalents", "INVALID_ACCURACY", "WARNING"),
            (
                ACCESSIONS[0],
                "operating_cash_flow",
                "MISSING_PREVIOUS_CUMULATIVE",
                "WARNING",
            ),
            (
                ACCESSIONS[0],
                "capital_expenditures",
                "MISSING_PREVIOUS_CUMULATIVE",
                "WARNING",
            ),
        }
        assert not any(
            row["metric_code"] == "cash_and_equivalents"
            and row["accession_number"] == ACCESSIONS[0]
            for row in metrics
        )
        invalid = next(row for row in issues if row["issue_code"] == "INVALID_ACCURACY")
        assert any(
            facts[identity]["decimals"] == "bad"
            for identity in invalid["source_occurrence_ids"]
        )
        for issue in issues:
            assert issue["source_occurrence_ids"] and issue["details"]
            for identity in issue["source_occurrence_ids"]:
                assert facts[identity]["accession_number"] == issue["accession_number"]
        for row, count, reported, warnings, focus in zip(
            filings, (9, 12, 12), (9, 10, 8), (3, 0, 0), ("Q2", "Q3", "FY"), strict=True
        ):
            assert (
                row["financial_metric_row_count"] == count
                and row["reported_metric_row_count"] == reported
            )
            assert row["derived_metric_row_count"] == count - reported
            assert (
                row["gold_quality_status"] == "COMPLETE"
                and row["gold_quality_reason"] == "CRITICAL_METRICS_PRESENT"
            )
            assert (
                row["missing_critical_metric_codes"] == []
                and row["present_critical_metric_count"] == 5
            )
            assert (
                row["quality_issue_count"] == row["warning_issue_count"] == warnings
                and row["error_issue_count"] == 0
            )
            assert (
                row["fiscal_year_focus"] == 2025 and row["fiscal_period_focus"] == focus
            )
            assert (
                row["activated_at"].tzinfo is not None
                and row["activated_at"].utcoffset() is not None
            )
    return {
        "status": "COMPLETE",
        "synthetic": True,
        "cik": CIK,
        "resources": resources,
        "counts": {
            "financial_metrics": len(metrics),
            "reported": len(metrics) - len(derived),
            "derived": len(derived),
            "filings": len(filings),
            "issues": len(issues),
        },
        "filings": filings,
        "financial_metrics": metrics,
        "quality_issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    work_dir = args.work_dir.resolve()
    if work_dir.is_relative_to(root):
        raise ValueError("Use a temporary directory outside the checkout")
    if work_dir.exists() and any(work_dir.iterdir()):
        raise ValueError("Synthetic work directory must be empty")
    os.environ.pop("SEC_USER_AGENT", None)
    os.environ["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"
    summary: dict[str, Any] = {"status": "FAILED", "synthetic": True}
    try:
        database = build_synthetic_catalog(work_dir)
        os.environ["SEC_EDGAR_DUCKDB_PATH"] = str(database)
        profiles_directory = work_dir / "profiles"
        profiles_directory.mkdir()
        # dbt's worker count does not limit DuckDB's engine workers.
        write_json(
            profiles_directory / "profiles.yml",
            {
                "sec_edgar_gold": {
                    "target": "dev",
                    "outputs": {
                        "dev": {
                            "type": "duckdb",
                            "path": "{{ env_var('SEC_EDGAR_DUCKDB_PATH') }}",
                            "schema": "gold",
                            "threads": _RESOURCE_BUDGET["dbt_concurrency"],
                            "settings": _DUCKDB_SETTINGS,
                        }
                    },
                }
            },
        )
        write_json(
            work_dir / "resource-summary.json",
            {
                "configured_budget": _RESOURCE_BUDGET,
                "profiles_path": str(profiles_directory / "profiles.yml"),
            },
        )
        command = [
            sys.executable,
            "-m",
            "dbt.cli.main",
            "debug",
            "--project-dir",
            str(root / "transform"),
            "--profiles-dir",
            str(profiles_directory),
            "--log-path",
            str(work_dir / "debug/logs"),
        ]
        work_dir.joinpath("debug").mkdir()
        with (
            (work_dir / "debug/stdout.log").open("wb") as stdout,
            (work_dir / "debug/stderr.log").open("wb") as stderr,
        ):
            subprocess.run(
                command,
                cwd=work_dir,
                stdout=stdout,
                stderr=stderr,
                timeout=120,
                check=True,
            )
        build = run_dbt_build(
            database_path=database,
            project_directory=root / "transform",
            profiles_directory=profiles_directory,
            artifact_directory=work_dir / "build",
            timeout_seconds=1500,
        )
        write_json(work_dir / "build-summary.json", asdict(build))
        assert build.status == "COMPLETE", build.error_message
        summary = verify_gold(database)
        summary["dbt_invocation_id"] = build.invocation_id
        summary["node_status_counts"] = dict(build.node_status_counts)
        print(summary["counts"])
        print("Synthetic Gold validation: COMPLETE")
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        if work_dir.is_dir():
            write_json(work_dir / "synthetic-summary.json", summary)


if __name__ == "__main__":
    main()
