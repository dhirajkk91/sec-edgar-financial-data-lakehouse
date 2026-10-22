"""The hosted dashboard: its entry point, committed demo database and pins.

Streamlit Community Cloud installs only deploy/requirements.txt and runs
deploy/streamlit_app.py from a fresh clone, so these check exactly those files.
"""

import hashlib
import importlib.util
import json
import re
import tomllib
from pathlib import Path

import duckdb
import pytest

from sec_edgar_lakehouse.dashboard.demo_export import DEMO_TABLES

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "sec_edgar_demo.duckdb"


def test_committed_demo_matches_its_manifest() -> None:
    manifest = json.loads((ROOT / "demo" / "sec_edgar_demo.json").read_text())

    assert manifest["sha256"] == hashlib.sha256(DEMO.read_bytes()).hexdigest()
    with duckdb.connect(str(DEMO), read_only=True) as connection:
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='gold' AND table_type='BASE TABLE'"
        ).fetchall()
        counts = {
            table: connection.sql(f"SELECT count(*) FROM gold.{table}").fetchall()[0][0]
            for table in DEMO_TABLES
        }
    assert sorted(name for (name,) in tables) == sorted(DEMO_TABLES)
    assert counts == manifest["row_counts"]


def test_requirements_match_the_lockfile() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {package["name"]: package["version"] for package in lock["package"]}
    pins = re.findall(
        r"^([A-Za-z0-9_.-]+)==([^\s;]+)",
        (ROOT / "deploy" / "requirements.txt").read_text(),
        flags=re.MULTILINE,
    )

    assert {"duckdb", "streamlit", "httpx"} <= {name for name, _ in pins}
    assert [
        (name, version) for name, version in pins if locked.get(name) != version
    ] == []


def test_entry_point_resolves_the_demo_database() -> None:
    pytest.importorskip("streamlit")
    spec = importlib.util.spec_from_file_location(
        "hosted_app", ROOT / "deploy" / "streamlit_app.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.database_path("") == DEMO.resolve()
    assert module.database_path(" demo/other.duckdb ") == ROOT / "demo" / "other.duckdb"
    assert module.database_path("/tmp/x.duckdb") == Path("/tmp/x.duckdb").resolve()


def test_hosted_app_opens_both_views_from_the_demo() -> None:
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(
        str(ROOT / "deploy" / "streamlit_app.py"), default_timeout=30
    ).run()

    assert not app.exception and not app.error
    assert [title.value for title in app.title] == [
        "Company financials from SEC filings"
    ]
    app.sidebar.radio(key="view").set_value("Trace a filing").run()
    assert not app.exception and not app.error
    assert [title.value for title in app.title] == ["SEC filing explorer"]
