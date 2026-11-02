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
        "sync": Command(
            "pipeline.universe_sync",
            "Process new 10-K/10-Q filings for a universe, then build Gold once.",
        ),
        "cohort": Command(
            "pipeline.cohort_run",
            "Plan, execute or resume the frozen 20-company analysis cohort.",
        ),
    },
    "Share": {
        "publish-lake": Command(
            "lake.publish",
            "Publish Bronze, Silver and Gold to S3 and Iceberg (or a local lake).",
        ),
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
