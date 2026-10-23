"""The `sec-edgar` command forwards to each tool's own `main` unchanged."""

import ast
import importlib
import sys
import tomllib
from pathlib import Path

import pytest

from sec_edgar_lakehouse import cli

ROOT = Path(__file__).resolve().parents[1]


def test_every_command_line_tool_has_a_subcommand() -> None:
    """A new module with its own command line must be added to `sec-edgar` too."""
    tools = set()
    for path in (ROOT / "src" / "sec_edgar_lakehouse").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        runs_as_script = any(
            isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)
            for node in tree.body
        )
        if runs_as_script and path.stem != "cli":
            relative = path.relative_to(ROOT / "src" / "sec_edgar_lakehouse")
            tools.add(".".join(relative.with_suffix("").parts))

    assert tools == {command.module for command in cli.COMMANDS.values()}


def test_script_entry_point() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert project["project"]["scripts"] == {
        "sec-edgar": "sec_edgar_lakehouse.cli:main"
    }


@pytest.mark.parametrize("name", sorted(cli.COMMANDS))
def test_each_subcommand_shows_its_own_help(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = list(sys.argv)

    # Some tools let argparse exit; others catch that and return its code.
    try:
        code: object = cli.main([name, "--help"])
    except SystemExit as exc:
        code = exc.code

    assert code == 0
    assert capsys.readouterr().out.startswith(f"usage: sec-edgar {name} ")
    assert sys.argv == argv


def test_usage_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    shown = capsys.readouterr().out

    assert all(f"  {name} " in shown for name in cli.COMMANDS)
    assert cli.main([]) == 2
    assert "usage: sec-edgar <command>" in capsys.readouterr().err


def test_unknown_command_fails_without_importing_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(name: str) -> None:
        raise AssertionError(f"Unexpected import: {name}")

    monkeypatch.setattr(importlib, "import_module", forbidden)

    assert cli.main(["unknown"]) == 2
    assert "unknown command 'unknown'" in capsys.readouterr().err


def test_exit_code_and_output_come_from_the_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.duckdb"

    assert cli.main(["export-demo", "--database", str(missing)]) == 1

    assert capsys.readouterr().err.startswith("FAILED: Source database does not exist")


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0

    assert capsys.readouterr().out.startswith("sec-edgar ")
