"""Publish a small local lakehouse to a file:// lake and read it back with Iceberg.

The data root holds two Bronze filings, their Silver Parquet versions, a catalog
database and the seven Gold tables. Publishing twice checks the incremental
rules: Bronze uploads once, Silver writes only changed filings, Gold replaces
itself, and every publish is a new Iceberg snapshot.
"""

import gzip
import hashlib
import json
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("pyiceberg")

from sec_edgar_lakehouse.lake import publish as lake
from tests.dashboard.test_demo_export import build_source

FILINGS = {
    "0000000012-25-000002": ("0000000012", "run-a"),
    "0000000034-25-000005": ("0000000034", "run-b"),
}


def write_silver(root: Path, accession: str, run_id: str, facts: int) -> Path:
    """A Silver version folder with facts, one dimension per fact, and no rejections."""
    cik = FILINGS[accession][0]
    version = root / f"silver/cik={cik}/accession={accession}/versions/{run_id}"
    version.mkdir(parents=True)
    with duckdb.connect() as connection:
        connection.execute(
            f"COPY (SELECT '{accession}-' || i AS source_occurrence_id, '{cik}' AS cik, "
            f"'{accession}' AS accession_number, i::DECIMAL(38,18) AS value_decimal "
            f"FROM range({facts}) t(i)) TO '{version}/facts.parquet'"
        )
        connection.execute(
            f"COPY (SELECT '{accession}-' || i AS source_occurrence_id, 'Axis' AS axis_name "
            f"FROM range({facts}) t(i)) TO '{version}/fact_dimensions.parquet'"
        )
        connection.execute(
            f"COPY (SELECT '{accession}-x' AS source_occurrence_id, '{cik}' AS cik, "
            f"'{accession}' AS accession_number, 'BAD' AS reason_code WHERE false) "
            f"TO '{version}/rejected_facts.parquet'"
        )
    return version


def set_active(database: Path, versions: dict[str, tuple[str, Path]]) -> None:
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS silver")
        connection.execute(
            "CREATE OR REPLACE TABLE silver.active_filings "
            "(accession_number VARCHAR, activation_run_id VARCHAR, version_path VARCHAR)"
        )
        for accession, (run_id, path) in versions.items():
            connection.execute(
                "INSERT INTO silver.active_filings VALUES (?, ?, ?)",
                [accession, run_id, str(path)],
            )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    database = build_source(tmp_path)
    root = database.parents[1]
    versions = {}
    for accession, (cik, run_id) in FILINGS.items():
        filing = root / f"bronze/cik={cik}/accession={accession}"
        (filing / "manifests").mkdir(parents=True)
        (filing / "manifests/run_id=1.json").write_text('{"status": "COMPLETE"}')
        (filing / "sec-derived").mkdir()
        (filing / "sec-derived/x_htm.xml").write_bytes(b"<xbrl/>")
        versions[accession] = (run_id, write_silver(root, accession, run_id, 3))
    set_active(database, versions)
    return root


def test_publish_writes_every_layer_and_is_incremental(
    root: Path, tmp_path: Path
) -> None:
    target = lake.open_target(f"file://{tmp_path / 'lake'}")
    first = lake.run_publish(root, target)

    objects = tmp_path / "lake/objects"
    archived = objects / "bronze/cik=0000000012/accession=0000000012-25-000002"
    assert gzip.decompress((archived / "sec-derived/x_htm.xml.gz").read_bytes()) == (
        b"<xbrl/>"
    )
    metadata = json.loads(
        (archived / "sec-derived/x_htm.xml.gz.metadata.json").read_text()
    )
    assert metadata["sha256"] == hashlib.sha256(b"<xbrl/>").hexdigest()
    assert first["bronze_filings_uploaded"] == 2
    assert first["silver_rows_written"] == {
        "facts": 6,
        "fact_dimensions": 6,
        "rejected_facts": 0,
    }
    assert set(first["gold_rows"]) == set(lake.DEMO_TABLES)
    assert (objects / lake.SNAPSHOT_KEY).is_file()
    gold_snapshots = len(
        target.catalog.load_table(("sec_gold", "dim_filings")).snapshots()
    )

    facts = target.catalog.load_table(("sec_silver", "facts"))
    assert facts.scan().to_arrow().num_rows == 6
    assert [f.name for f in facts.spec().fields] == ["cik"]
    dimensions = target.catalog.load_table(("sec_silver", "fact_dimensions"))
    assert set(dimensions.scan().to_arrow().column("cik").to_pylist()) == {
        "0000000012",
        "0000000034",
    }
    gold = target.catalog.load_table(("sec_gold", "dim_filings"))
    paths = gold.scan().to_arrow().column("version_path").to_pylist()
    assert all(not path.startswith("/") for path in paths if path)

    # Night two: one filing is re-parsed with more facts; the other is unchanged.
    accession = "0000000034-25-000005"
    database = root / "query/sec_edgar.duckdb"
    reparsed = write_silver(root, accession, "run-c", 5)
    set_active(
        database,
        {
            "0000000012-25-000002": (
                "run-a",
                root
                / "silver/cik=0000000012/accession=0000000012-25-000002/versions/run-a",
            ),
            accession: ("run-c", reparsed),
        },
    )
    second = lake.run_publish(root, lake.open_target(f"file://{tmp_path / 'lake'}"))

    assert second["bronze_filings_uploaded"] == 0
    assert second["silver_rows_written"]["facts"] == 5
    facts = target.catalog.load_table(("sec_silver", "facts"))
    rows = facts.scan().to_arrow()
    assert rows.num_rows == 8
    assert rows["accession_number"].to_pylist().count(accession) == 5
    # Gold was replaced, and the first night's snapshot is still there to query.
    gold = target.catalog.load_table(("sec_gold", "dim_filings"))
    assert len(gold.snapshots()) > gold_snapshots
    first_night = gold.snapshots()[gold_snapshots - 1].snapshot_id
    assert gold.scan(snapshot_id=first_night).to_arrow().num_rows == 2


def test_failed_gold_build_keeps_the_last_good_gold(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = lake.open_target(f"file://{tmp_path / 'lake'}")
    lake.run_publish(root, target)
    objects = tmp_path / "lake/objects"
    served = (objects / lake.SNAPSHOT_KEY).read_bytes()
    gold_snapshots = len(
        target.catalog.load_table(("sec_gold", "dim_filings")).snapshots()
    )
    (root / "sync-runs").mkdir()
    (root / "sync-runs/latest.json").write_text(
        json.dumps({"status": "FAILED", "gold": {"status": "FAILED"}})
    )
    database = root / "query/sec_edgar.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM gold.dim_filings")

    code = lake.main(
        ["--data-root", str(root), "--lake", f"file://{tmp_path / 'lake'}"]
    )

    assert code == 0
    assert "Gold not published" in capsys.readouterr().out
    assert (objects / lake.SNAPSHOT_KEY).read_bytes() == served
    gold = target.catalog.load_table(("sec_gold", "dim_filings"))
    assert len(gold.snapshots()) == gold_snapshots
    assert gold.scan().to_arrow().num_rows == 2
    published = json.loads((objects / "runs/latest-publish.json").read_text())
    assert published["status"] == "PARTIAL" and published["gold_rows"] == {}


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (None, None),
        ('{"gold": {"status": "COMPLETE"}}', "COMPLETE"),
        ('{"gold": {"status": "SKIPPED"}}', "SKIPPED"),
        ('{"gold": {"status": "FAILED"}}', "FAILED"),
        ("not json", "FAILED"),
        ('{"gold": "odd"}', "FAILED"),
    ],
)
def test_last_gold_build_reads_the_sync_summary(
    tmp_path: Path, summary: str | None, expected: str | None
) -> None:
    if summary is not None:
        (tmp_path / "sync-runs").mkdir()
        (tmp_path / "sync-runs/latest.json").write_text(summary)
    assert lake.last_gold_build(tmp_path) == expected


def test_nothing_to_publish_without_a_catalog(tmp_path: Path) -> None:
    target = lake.open_target(f"file://{tmp_path / 'lake'}")

    with pytest.raises(lake.LakePublishError, match="run sync first"):
        lake.run_publish(tmp_path / "empty", target)


def test_lake_uri_must_be_s3_or_file() -> None:
    with pytest.raises(lake.LakePublishError, match="s3://bucket/prefix"):
        lake.open_target("gs://bucket/x")
