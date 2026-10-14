"""Run this file with Streamlit to open the local filing explorer.

SEC_EDGAR_DUCKDB_PATH can choose another existing database. Relative paths
are resolved from this repository, so starting Streamlit in a different
folder does not change which database the page reads.
"""

import os
from pathlib import Path

from sec_edgar_lakehouse.dashboard.dashboard_app import main

if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parent
    configured = os.environ.get("SEC_EDGAR_DUCKDB_PATH", "").strip()
    database_path = (
        Path(configured) if configured else Path("data/query/sec_edgar.duckdb")
    )
    # A relative setting means relative to this repository, not the shell's current folder.
    if not database_path.is_absolute():
        database_path = repository_root / database_path
    main(database_path=database_path.resolve())
