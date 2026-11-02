"""The exploratory notebook runs from top to bottom on the committed demo database.

The committed copy must also be saved with its outputs, so it reads on GitHub
without running, and must not carry paths from the machine that ran it.
"""

import json
import re
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "notebooks" / "exploratory-analysis.ipynb"


def test_notebook_runs_without_errors() -> None:
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    client = nbclient.NotebookClient(
        notebook, timeout=300, resources={"metadata": {"path": str(NOTEBOOK.parent)}}
    )

    client.execute()

    errors = [
        output
        for cell in notebook.cells
        for output in cell.get("outputs", [])
        if output.output_type == "error"
    ]
    assert errors == []


def test_committed_notebook_has_outputs_and_no_local_paths() -> None:
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]

    assert code and all(cell.get("outputs") for cell in code)
    text = json.dumps(notebook)
    assert not re.search(r"/(Users|home|tmp|private)/", text)
    assert "@" not in "".join(
        output.get("text", "") for cell in code for output in cell["outputs"]
    )
