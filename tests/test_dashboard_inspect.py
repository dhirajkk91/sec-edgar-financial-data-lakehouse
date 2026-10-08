import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from test_dashboard_read import (
    ACCESSION_A,
    ACCESSION_B,
    CIK_A,
    CIK_B,
    EXACT,
    create_database,
    insert_record,
    populated_database,
)

from sec_edgar_lakehouse.dashboard_inspect import main


@pytest.mark.parametrize(
    "filters,scope,counts",
    [
        ([], "all companies", (3, 2, 3)),
        (["--cik", "12"], f"CIK {CIK_A}", (2, 1, 2)),
        (
            ["--cik", "34", "--accession-number", ACCESSION_B],
            f"CIK {CIK_B} / accession {ACCESSION_B}",
            (1, 1, 1),
        ),
        (["--cik", "56"], "CIK 0000000056", (0, 0, 0)),
    ],
)
def test_scope_counts_output_and_no_writes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    filters: list[str],
    scope: str,
    counts: tuple[int, int, int],
) -> None:
    path = populated_database(tmp_path / "dashboard.duckdb")
    before = path.read_bytes()
    files = set(tmp_path.rglob("*"))
    assert main(["--database", str(path), *filters]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert f"Database: {path.resolve()}" in captured.out
    assert f"Available CIKs: {CIK_A}, {CIK_B}" in captured.out
    assert f"Scope: {scope}" in captured.out
    for label, count in zip(("Filings", "Financial metrics", "Quality issues"), counts):
        assert f"{label}: {count}" in captured.out
    assert "COMPLETE" not in captured.out
    assert captured.out.count("Metric:") == counts[1]
    assert path.read_bytes() == before
    assert set(tmp_path.rglob("*")) == files


def test_exact_samples_include_period_origin_and_units_and_stop_at_five(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")
    for number in range(7):
        insert_record(
            path,
            "fct_financial_metrics",
            metric_code=f"metric_{number}",
            period_label="derived_quarter" if number == 0 else "instant",
            value_origin="derived" if number == 0 else "reported",
            unit_expression="USD/share" if number == 1 else "USD",
            period_start=None,
            period_end=None,
        )
    assert main(["--database", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Financial metrics: 7" in output
    assert output.count("Metric:") == 5
    assert "metric_4" in output and "metric_5" not in output
    assert f"Value: {EXACT}" in output
    assert "Period: derived_quarter" in output and "Period: instant" in output
    assert "Origin: derived" in output and "Origin: reported" in output
    assert "Start: None" in output and "End: None" in output
    assert "Instant: 2025-06-30" in output
    assert "Units: USD/share" in output


def test_empty_database_is_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")
    assert main(["--database", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Available CIKs: none" in output
    assert "Filings: 0" in output and "Financial metrics: 0" in output
    assert "Quality issues: 0" in output and "Metric:" not in output


@pytest.mark.parametrize(
    "arguments",
    [[], ["--database"], ["--unknown"], ["--database", "path", "--cik"]],
)
def test_invalid_invocation_returns_one(
    capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    assert main(arguments) == 1
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "filters",
    [
        ["--cik", "0"],
        ["--accession-number", ACCESSION_A],
        ["--cik", "12", "--accession-number", "bad"],
    ],
)
def test_invalid_filters_return_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filters: list[str]
) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")
    assert main(["--database", str(path), *filters]) == 1
    captured = capsys.readouterr()
    assert "Dashboard read failure:" in captured.err and captured.out == ""


def test_missing_database_and_schema_failures_return_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "absent" / "dashboard.duckdb"
    assert main(["--database", str(missing)]) == 1
    assert not missing.parent.exists()
    assert "Dashboard read failure:" in capsys.readouterr().err
    path = create_database(tmp_path / "dashboard.duckdb")
    with duckdb.connect(str(path)) as connection:
        connection.execute("DROP TABLE gold.metric_quality_issues")
    assert main(["--database", str(path)]) == 1
    assert "gold.metric_quality_issues" in capsys.readouterr().err


def test_help_and_default_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["--help"]) == 0
    assert "--database" in capsys.readouterr().out
    path = create_database(tmp_path / "dashboard.duckdb")
    monkeypatch.setattr(sys, "argv", ["dashboard_inspect", "--database", str(path)])
    assert main() == 0
    assert "Scope: all companies" in capsys.readouterr().out


def test_module_entry_point_exit_codes(tmp_path: Path) -> None:
    path = create_database(tmp_path / "dashboard.duckdb")
    successful = subprocess.run(
        [
            sys.executable,
            "-m",
            "sec_edgar_lakehouse.dashboard_inspect",
            "--database",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert successful.returncode == 0 and "Financial metrics: 0" in successful.stdout
    invalid = subprocess.run(
        [sys.executable, "-m", "sec_edgar_lakehouse.dashboard_inspect"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode == 1 and "error:" in invalid.stderr
