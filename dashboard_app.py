"""Local Streamlit entrypoint; configured paths are relative to this repository."""

import os
from pathlib import Path

from sec_edgar_lakehouse.dashboard_app import main

if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parent
    configured = os.environ.get("SEC_EDGAR_DUCKDB_PATH", "").strip()
    database_path = (
        Path(configured) if configured else Path("data/query/sec_edgar.duckdb")
    )
    if not database_path.is_absolute():
        database_path = repository_root / database_path
    main(database_path=database_path.resolve())
