"""One command for the project's tools: ``sec-edgar <command> [options]``.

Each command runs an existing module's ``main`` with the remaining arguments,
so options, output and exit codes are the same as ``python -m <module>``.
Modules are imported only when their command runs, which keeps ``--help`` fast.
"""

import importlib
import importlib.metadata
import sys
from collections.abc import Sequence
from dataclasses import dataclass

PACKAGE = "sec_edgar_lakehouse"
DISTRIBUTION = "sec-edgar-financial-data-lakehouse"


@dataclass(frozen=True, slots=True)
class Command:
    """A subcommand: the module whose ``main`` it runs, and a one-line summary."""

    module: str
    summary: str


GROUPS: dict[str, dict[str, Command]] = {
    "Run the pipeline": {
        "cohort": Command(
            "pipeline.cohort_run",
            "Plan, execute or resume the fixed issuer cohort.",
        ),
        "pipeline": Command(
            "pipeline.end_to_end_pipeline_run",
            "Run one company from SEC filings through Gold, with a final summary.",
        ),
        "company": Command(
            "pipeline.company_pipeline_run",
            "Run one company through Bronze, Silver and the DuckDB catalog.",
        ),
    },
    "Run one stage": {
        "silver-publish": Command(
            "silver.silver_publish",
            "Extract one verified Bronze filing and publish it to Silver.",
        ),
        "catalog-refresh": Command(
            "catalog.silver_catalog_refresh",
            "Refresh the DuckDB catalog over active Silver filings.",
        ),
        "metadata-load": Command(
            "metadata.filing_metadata_load",
            "Load filing metadata from a company-run record.",
        ),
        "fiscal-load": Command(
            "metadata.filing_fiscal_metadata_load",
            "Load fiscal year and period metadata into the catalog.",
        ),
        "profiles-load": Command(
            "metadata.company_profile_load",
            "Load company profiles from saved company-run records.",
        ),
    },
    "Inspect, read only": {
        "inspect": Command(
            "dashboard.dashboard_inspect",
            "Print a Gold snapshot and a small exact financial sample.",
        ),
        "silver-inspect": Command(
            "silver.silver_inspect",
            "Extract one Bronze filing in memory and print a few facts.",
        ),
        "parquet-inspect": Command(
            "silver.silver_parquet_inspect",
            "Summarize one Silver Parquet output folder.",
        ),
        "fiscal-inspect": Command(
            "metadata.filing_fiscal_metadata_inspect",
            "Print fiscal metadata and its source evidence without saving it.",
        ),
    },
    "Share": {
        "export-demo": Command(
            "dashboard.demo_export",
            "Copy the Gold tables into the portable demo database.",
        ),
    },
}
COMMANDS = {
    name: command for group in GROUPS.values() for name, command in group.items()
}


def usage() -> str:
    """The command list, grouped the way the pipeline is used."""
    width = max(len(name) for name in COMMANDS)
    lines = [
        "usage: sec-edgar <command> [options]",
        "       sec-edgar <command> --help",
        "",
        "SEC EDGAR financial data lakehouse.",
    ]
    for title, commands in GROUPS.items():
        lines += ["", f"{title}:"]
        lines += [
            f"  {name.ljust(width)}  {command.summary}"
            for name, command in commands.items()
        ]
    lines += [
        "",
        "The dashboard is a Streamlit app: run `make dashboard` or",
        "`uv run --group dashboard streamlit run dashboard_app.py`.",
    ]
    return "\n".join(lines)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one subcommand and return its exit code."""
    args = list(sys.argv[1:] if arguments is None else arguments)
    if not args or args[0] in ("-h", "--help"):
        print(usage(), file=sys.stdout if args else sys.stderr)
        return 0 if args else 2
    if args[0] == "--version":
        print(f"sec-edgar {importlib.metadata.version(DISTRIBUTION)}")
        return 0
    name, rest = args[0], args[1:]
    command = COMMANDS.get(name)
    if command is None:
        print(f"sec-edgar: unknown command {name!r}\n\n{usage()}", file=sys.stderr)
        return 2
    module = importlib.import_module(f"{PACKAGE}.{command.module}")
    # argparse names the program after argv[0]; show the command that was typed.
    original = sys.argv[0] if sys.argv else ""
    sys.argv[:1] = [f"sec-edgar {name}"]
    try:
        return int(module.main(rest))
    finally:
        sys.argv[:1] = [original]


if __name__ == "__main__":
    raise SystemExit(main())
