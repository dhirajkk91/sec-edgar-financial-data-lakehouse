"""Run this file with Streamlit to open the local filing explorer.

SEC_EDGAR_DUCKDB_PATH can choose another existing database. Without it, a
local dashboard-current link takes precedence over the older default database.
Relative paths are resolved from this repository.
"""

import os
from pathlib import Path

from sec_edgar_lakehouse.dashboard.dashboard_app import main


def _database_path(repository_root: Path, configured: str) -> Path:
    """Choose an explicit path, the local cohort link, or the original default."""
    configured = configured.strip()
    current = Path("data/dashboard-current.duckdb")
    if configured:
        database_path = Path(configured)
    elif (repository_root / current).is_symlink():
        database_path = current
    else:
        database_path = Path("data/query/sec_edgar.duckdb")
    if not database_path.is_absolute():
        database_path = repository_root / database_path
    return database_path.resolve()


if __name__ == "__main__":
    main(
        database_path=_database_path(
            Path(__file__).resolve().parent,
            os.environ.get("SEC_EDGAR_DUCKDB_PATH", ""),
        )
    )
