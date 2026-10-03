"""Open the SEC lakehouse database in DuckDB's browser UI."""

from pathlib import Path
from threading import Event

import duckdb


def main() -> None:
    # Find the data beside this script, even if the terminal is in another folder.
    project_directory = Path(__file__).resolve().parent
    database_path = project_directory / "data" / "query" / "sec_edgar.duckdb"

    if not database_path.is_file():
        raise SystemExit(
            f"Database not found: {database_path}\n"
            "Put duckdb_startup.py in the repository root, beside pyproject.toml."
        )

    try:
        with duckdb.connect(str(database_path)) as connection:
            connection.execute("INSTALL ui")
            connection.execute("LOAD ui")
            connection.execute("CALL start_ui()")

            print(f"Database: {database_path}", flush=True)
            print("DuckDB UI: http://localhost:4213", flush=True)
            print("Keep this terminal open. Press Ctrl+C to stop.", flush=True)

            # The browser UI needs this Python process to stay alive.
            Event().wait()
    except KeyboardInterrupt:
        print("\nDuckDB UI stopped.")


if __name__ == "__main__":
    main()
