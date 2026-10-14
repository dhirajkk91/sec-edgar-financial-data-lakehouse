"""Run inside the fresh wheel environment; verify imports and exact read-only Gold rows."""

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import pkgutil
import subprocess
import sys
import tomllib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

import sec_edgar_lakehouse
from sec_edgar_lakehouse import load_dashboard_snapshot


def decode(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind.startswith("DECIMAL("):
        return Decimal(value)
    if kind == "DATE":
        return date.fromisoformat(value)
    if kind in ("TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"):
        return datetime.fromisoformat(value)
    if kind.endswith("[]"):
        return tuple(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--lockfile", required=True, type=Path)
    args = parser.parse_args()
    work_dir = args.work_dir.resolve()
    checkout = args.checkout.resolve()
    assert not Path.cwd().resolve().is_relative_to(checkout)
    assert "PYTHONPATH" not in os.environ and sys.flags.isolated
    for name in ("dbt", "streamlit", "pytest", "mypy", "ruff"):
        assert importlib.util.find_spec(name) is None, (
            f"Unexpected optional dependency: {name}"
        )
    installed = {
        distribution.metadata["Name"].lower().replace("_", "-"): distribution.version
        for distribution in importlib.metadata.distributions()
    }
    runtime_names = {
        line.split("==", 1)[0]
        for line in (work_dir / "runtime-requirements.txt").read_text().splitlines()
        if "==" in line and not line.startswith((" ", "#"))
    }
    assert set(installed) == runtime_names | {"sec-edgar-financial-data-lakehouse"}
    lock = tomllib.loads(args.lockfile.read_text())
    locked = {(package["name"], package["version"]) for package in lock["package"]}
    assert all((name, version) in locked for name, version in installed.items())
    exports = {
        name: value
        for name, value in vars(sec_edgar_lakehouse).items()
        if not name.startswith("_") and callable(value)
    }
    assert (
        len(exports) > 50
        and {
            "FilingReference",
            "extract_filing_facts",
            "run_dbt_build",
            "load_dashboard_snapshot",
        }
        <= exports.keys()
    )
    imported = []
    for module in pkgutil.walk_packages(
        sec_edgar_lakehouse.__path__, sec_edgar_lakehouse.__name__ + "."
    ):
        # The optional UI module belongs in the wheel but needs Streamlit to import.
        if module.name == "sec_edgar_lakehouse.dashboard.dashboard_app":
            continue
        value = importlib.import_module(module.name)
        path = Path(value.__file__).resolve()
        assert path.is_relative_to(
            Path(sys.prefix).resolve()
        ) and not path.is_relative_to(checkout)
        imported.append({"module": module.name, "path": str(path)})
    package_path = Path(sec_edgar_lakehouse.__file__).resolve()
    assert package_path.is_relative_to(
        Path(sys.prefix).resolve()
    ) and not package_path.is_relative_to(checkout)
    cli_modules = (
        "catalog.silver_catalog_refresh",
        "dashboard.dashboard_inspect",
        "metadata.filing_metadata_load",
        "metadata.filing_fiscal_metadata_load",
        "metadata.filing_fiscal_metadata_inspect",
        "silver.silver_inspect",
        "silver.silver_parquet_inspect",
        "silver.silver_publish",
        "pipeline.company_pipeline_run",
        "pipeline.end_to_end_pipeline_run",
    )
    with (work_dir / "cli-help.log").open("w", encoding="utf-8") as log:
        for module in cli_modules:
            subprocess.run(
                [sys.executable, "-I", "-m", f"sec_edgar_lakehouse.{module}", "--help"],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=True,
            )
    fixture = json.loads((work_dir / "gold-reader.json").read_text())
    assert fixture["synthetic"] is True
    database = work_dir / "reader.duckdb"
    expected = {}
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA gold")
        for name, table in fixture["tables"].items():
            assert name in (
                "dim_filings",
                "fct_financial_metrics",
                "metric_quality_issues",
            )
            columns, kinds = table["columns"], table["column_types"]
            definitions = ", ".join(
                f'"{column}" {kind}'
                for column, kind in zip(columns, kinds, strict=True)
            )
            connection.execute(f"CREATE TABLE gold.{name} ({definitions})")
            rows = tuple(
                tuple(
                    decode(value, kind) for value, kind in zip(row, kinds, strict=True)
                )
                for row in table["rows"]
            )
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO gold.{name} VALUES ({placeholders})", rows
            )
            expected[name] = rows
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    snapshot = load_dashboard_snapshot(database_path=database)
    for name, table in (
        ("dim_filings", snapshot.filings),
        ("fct_financial_metrics", snapshot.financial_metrics),
        ("metric_quality_issues", snapshot.quality_issues),
    ):
        assert table.rows == expected[name], f"Complete ordered {name} records changed"
        assert table.columns == tuple(fixture["tables"][name]["columns"])
    eps = next(
        dict(zip(snapshot.financial_metrics.columns, row, strict=True))
        for row in snapshot.financial_metrics.rows
        if row[snapshot.financial_metrics.columns.index("metric_code")] == "diluted_eps"
    )
    assert isinstance(eps["value_decimal"], Decimal) and eps[
        "value_decimal"
    ] == Decimal("1.230000000000000001")
    assert len(eps["current_supporting_occurrence_ids"]) == 2 and isinstance(
        eps["current_supporting_occurrence_ids"], tuple
    )
    timestamp = snapshot.filings.rows[0][snapshot.filings.columns.index("activated_at")]
    assert (
        isinstance(timestamp, datetime)
        and timestamp.tzinfo is not None
        and timestamp.utcoffset() is not None
    )
    with (
        (work_dir / "inspector.stdout.log").open("w", encoding="utf-8") as stdout,
        (work_dir / "inspector.stderr.log").open("w", encoding="utf-8") as stderr,
    ):
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "sec_edgar_lakehouse.dashboard.dashboard_inspect",
                "--database",
                str(database),
                "--cik",
                "0000000042",
            ],
            stdout=stdout,
            stderr=stderr,
            timeout=30,
            check=True,
        )
    output = (work_dir / "inspector.stdout.log").read_text()
    assert (
        "Filings: 1" in output
        and "Financial metrics: 9" in output
        and "Quality issues: 3" in output
    )
    assert "1.230000000000000001" in output
    after = hashlib.sha256(database.read_bytes()).hexdigest()
    assert before == after, "Read-only reader/inspector changed the database"
    summary = {
        "status": "COMPLETE",
        "synthetic": True,
        "python": sys.version,
        "installed_distributions": installed,
        "exact_runtime_distribution_set_verified": True,
        "public_exports": sorted(exports),
        "imports": imported,
        "package_path": str(package_path),
        "cli_help_modules": cli_modules,
        "filings": len(snapshot.filings.rows),
        "financial_metrics": len(snapshot.financial_metrics.rows),
        "issues": len(snapshot.quality_issues.rows),
        "exact_eps": str(eps["value_decimal"]),
        "ordered_eps_evidence": eps["current_supporting_occurrence_ids"],
        "activated_at": timestamp.isoformat(),
        "database_sha256_before": before,
        "database_sha256_after": after,
        "complete_records_equal": True,
        "dbt_absent": True,
        "streamlit_absent": True,
    }
    (work_dir / "probe-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
