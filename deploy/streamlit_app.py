"""Entry point for the hosted, read-only dashboard.

The host installs only deploy/requirements.txt, so the package is imported from
src instead of being installed.

With SEC_EDGAR_SNAPSHOT_S3_URI set (a Streamlit secret on the host), the app
reads the Gold snapshot the weeknight job publishes to S3. It checks the file
against the SHA-256 in its manifest, keeps it for an hour, and falls back to
the committed demo/sec_edgar_demo.duckdb if anything goes wrong, so the page
always opens. Without that setting it opens the demo, or SEC_EDGAR_DUCKDB_PATH
when set; relative paths are resolved from the repository.
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

# Imported after the path change above, because the package is not installed.
import streamlit as st

from sec_edgar_lakehouse.dashboard.dashboard_app import main

DEMO_DATABASE = Path("demo/sec_edgar_demo.duckdb")
SNAPSHOT_HOURS = 1


def database_path(configured: str) -> Path:
    """Use the configured database, or the committed demo file by default."""
    path = Path(configured.strip()) if configured.strip() else DEMO_DATABASE
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def download_snapshot(uri: str, client: Any = None) -> tuple[Path, str]:
    """Fetch the snapshot and its manifest from S3; return the file and its date."""
    import boto3  # type: ignore[import-untyped]

    parsed = urlparse(uri)
    if (
        parsed.scheme != "s3"
        or not parsed.netloc
        or not parsed.path.endswith(".duckdb")
    ):
        raise ValueError("SEC_EDGAR_SNAPSHOT_S3_URI must be s3://bucket/key.duckdb")
    bucket, key = parsed.netloc, parsed.path.lstrip("/")
    s3 = client or boto3.client("s3")
    manifest = json.loads(
        s3.get_object(Bucket=bucket, Key=key.removesuffix(".duckdb") + ".json")[
            "Body"
        ].read()
    )
    folder = Path(tempfile.mkdtemp(prefix="sec-edgar-snapshot-"))
    destination = folder / "sec_edgar_gold.duckdb"
    s3.download_file(bucket, key, str(destination))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if digest != manifest.get("sha256"):
        raise ValueError("Snapshot checksum does not match its manifest")
    return destination, str(manifest.get("exported_at", "unknown"))


@st.cache_resource(ttl=SNAPSHOT_HOURS * 3600, show_spinner="Loading the latest data…")
def live_snapshot(uri: str) -> tuple[Path, str] | None:
    """The latest published snapshot, or None so the caller can use the demo."""
    try:
        return download_snapshot(uri)
    except Exception as exc:  # noqa: BLE001  # The demo is always a safe page to show.
        print(f"Snapshot unavailable, using the demo: {exc}", file=sys.stderr)
        return None


def choose_database() -> tuple[Path, str | None]:
    uri = os.environ.get("SEC_EDGAR_SNAPSHOT_S3_URI", "").strip()
    if uri and (snapshot := live_snapshot(uri)) is not None:
        path, exported_at = snapshot
        return path, f"Live data, published {exported_at[:16].replace('T', ' ')} UTC"
    path = database_path(os.environ.get("SEC_EDGAR_DUCKDB_PATH", ""))
    note = (
        "Demo snapshot: 20 companies, October 2026"
        if path == database_path("")
        else None
    )
    return path, note


if __name__ == "__main__":
    chosen, note = choose_database()
    main(database_path=chosen, source_note=note)
