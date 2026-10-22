"""The demo export copies only the Gold tables and publishes no local details.

A small source database sits under '<root>/query/', like a real cohort, and
its filing paths are absolute. The export must make them relative, keep every
row, and refuse to publish anything that still looks local or personal.
"""

import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from sec_edgar_lakehouse.dashboard.dashboard_read import (
    _COMPANIES_SCHEMA,
    _COMPANY_FINANCIALS_SCHEMA,
    load_company_snapshot,
    load_dashboard_snapshot,
)
from sec_edgar_lakehouse.dashboard.demo_export import (
    DEMO_TABLES,
    DemoExportError,
    export_demo_database,
    main,
)
from tests.dashboard.test_dashboard_read import (
    ACCESSION_B,
    CIK_B,
    create_database,
    insert_record,
)

SILVER = "silver/cik=0000000012/accession=0000000012-25-000002/versions/v1"


def build_source(tmp_path: Path) -> Path:
    """Two filings: one recorded under this root, one under another machine's root."""
    root = tmp_path / "cohort"
    (root / "query").mkdir(parents=True)
    source = create_database(root / "query" / "sec_edgar.duckdb")
    insert_record(
        source,
        "dim_filings",
        version_path=f"{root}/{SILVER}",
        publication_path=f"{root}/{SILVER}/publication.json",
    )
    insert_record(
        source,
        "dim_filings",
        cik=CIK_B,
        accession_number=ACCESSION_B,
        version_path="/Users/someone/project/data/cohort/silver/cik=0000000034/v1",
        publication_path=None,
    )
    insert_record(source, "fct_financial_metrics", unit_expression="USD")
    insert_record(source, "metric_quality_issues", details="Concept differs")
    with duckdb.connect(str(source)) as connection:
        for name, schema in (
            ("dim_companies", _COMPANIES_SCHEMA),
            ("fct_company_financials", _COMPANY_FINANCIALS_SCHEMA),
        ):
            fields = ", ".join(f"{column} {kind}" for column, kind in schema)
            connection.execute(f"CREATE TABLE gold.{name} ({fields})")
    return source


def test_export_copies_gold_tables_with_relative_paths(tmp_path: Path) -> None:
    source = build_source(tmp_path)
    destination = tmp_path / "demo" / "sec_edgar_demo.duckdb"

    result = export_demo_database(source=source, destination=destination)

    assert result.row_counts == {
        "dim_filings": 2,
        "fct_financial_metrics": 1,
        "metric_quality_issues": 1,
        "dim_companies": 0,
        "fct_company_financials": 0,
    }
    with duckdb.connect(str(destination), read_only=True) as connection:
        tables = connection.execute(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables "
            "ORDER BY table_name"
        ).fetchall()
        paths = connection.execute(
            "SELECT version_path, publication_path FROM gold.dim_filings ORDER BY cik"
        ).fetchall()
    assert tables == sorted(
        [("gold", name, "BASE TABLE") for name in DEMO_TABLES], key=lambda t: t[1]
    )
    assert paths == [
        (SILVER, f"{SILVER}/publication.json"),
        ("silver/cik=0000000034/v1", None),
    ]
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert manifest["row_counts"] == result.row_counts
    assert str(tmp_path) not in result.manifest.read_text(encoding="utf-8")
    assert len(load_dashboard_snapshot(database_path=destination).filings.rows) == 2
    load_company_snapshot(database_path=destination)
    assert sorted(path.name for path in destination.parent.iterdir()) == [
        "sec_edgar_demo.duckdb",
        "sec_edgar_demo.json",
    ]


@pytest.mark.parametrize(
    ("details", "label"),
    [
        ("Read from /home/someone/notes.txt", "a local path"),
        (r"Read from C:\Users\someone\notes.txt", "a local path"),
        ("Contact someone@example.com", "an email address"),
    ],
)
def test_local_or_personal_text_is_refused(
    tmp_path: Path, details: str, label: str
) -> None:
    source = build_source(tmp_path)
    insert_record(source, "metric_quality_issues", details=details)
    destination = tmp_path / "demo" / "sec_edgar_demo.duckdb"

    with pytest.raises(DemoExportError, match=label):
        export_demo_database(source=source, destination=destination)

    assert list(destination.parent.iterdir()) == []


def test_urls_and_sec_names_are_not_mistaken_for_paths(tmp_path: Path) -> None:
    source = build_source(tmp_path)
    insert_record(
        source,
        "fct_financial_metrics",
        accession_number="0000000012-25-000009",
        unit_expression="{http://www.xbrl.org/2003/iso4217}USD",
        concept_namespace="http://fasb.org/us-gaap/2024",
    )
    insert_record(source, "metric_quality_issues", details="FORD MOTOR CO /DE/")

    result = export_demo_database(
        source=source, destination=tmp_path / "demo" / "demo.duckdb"
    )

    assert result.row_counts["fct_financial_metrics"] == 2


def test_unrecognized_absolute_path_is_refused(tmp_path: Path) -> None:
    source = build_source(tmp_path)
    insert_record(
        source,
        "dim_filings",
        accession_number="0000000012-25-000003",
        version_path="/srv/lakehouse/cik=0000000012/v1",
        publication_path=None,
    )

    with pytest.raises(DemoExportError, match="non-relative path"):
        export_demo_database(
            source=source, destination=tmp_path / "demo" / "demo.duckdb"
        )


def test_gold_views_are_refused(tmp_path: Path) -> None:
    source = build_source(tmp_path)
    with duckdb.connect(str(source)) as connection:
        connection.execute("ALTER TABLE gold.dim_companies RENAME TO companies_table")
        connection.execute(
            "CREATE VIEW gold.dim_companies AS SELECT * FROM gold.companies_table"
        )
    destination = tmp_path / "demo" / "demo.duckdb"

    with pytest.raises(DemoExportError, match="is a VIEW"):
        export_demo_database(source=source, destination=destination)

    assert not destination.parent.exists()


def test_existing_demo_needs_replace(tmp_path: Path) -> None:
    source = build_source(tmp_path)
    destination = tmp_path / "demo" / "demo.duckdb"
    export_demo_database(source=source, destination=destination)

    with pytest.raises(DemoExportError, match="exists"):
        export_demo_database(source=source, destination=destination)
    rebuilt = export_demo_database(source=source, destination=destination, replace=True)

    assert rebuilt.row_counts["dim_filings"] == 2


def test_cli_reports_failure_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.duckdb"

    assert (
        main(["--database", str(missing), "--output", str(tmp_path / "d.duckdb")]) == 1
    )

    assert capsys.readouterr().err.startswith("FAILED: Source database does not exist")
