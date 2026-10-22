"""Entry point for the hosted, read-only dashboard.

The host installs only deploy/requirements.txt, so the package is imported from
src instead of being installed. The app opens demo/sec_edgar_demo.duckdb, a
small file that holds just the five Gold tables. SEC_EDGAR_DUCKDB_PATH can
choose another database; relative paths are resolved from the repository.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

# Imported after the path change above, because the package is not installed.
from sec_edgar_lakehouse.dashboard.dashboard_app import main

DEMO_DATABASE = Path("demo/sec_edgar_demo.duckdb")


def database_path(configured: str) -> Path:
    """Use the configured database, or the committed demo file by default."""
    path = Path(configured.strip()) if configured.strip() else DEMO_DATABASE
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


if __name__ == "__main__":
    main(database_path=database_path(os.environ.get("SEC_EDGAR_DUCKDB_PATH", "")))
