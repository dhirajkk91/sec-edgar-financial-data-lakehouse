from pathlib import Path

import pytest
from test_silver_catalog import published

from sec_edgar_lakehouse.silver_catalog_refresh import main


@pytest.mark.parametrize(
    "status,code", [("COMPLETE", 0), ("PARTIAL", 2), ("FAILED", 1)]
)
def test_command_summary_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], status: str, code: int
) -> None:
    root = tmp_path / "silver space"
    root.mkdir()
    if status != "FAILED":
        published(tmp_path, root, 1)
    if status == "PARTIAL":
        bad = published(tmp_path, root, 2)
        bad.active_path.write_text("bad")
    db = tmp_path / "query space" / "catalog.duckdb"
    assert main(["--silver-directory", str(root), "--database", str(db)]) == code
    output = capsys.readouterr()
    if status == "FAILED":
        assert output.out == ""
        assert "FAILED:" in output.err and "Traceback" not in output.err
    else:
        assert output.err == ""
        assert f"Refresh status: {status}" in output.out
        assert str(db.resolve()) in output.out
        assert "Active filings: 1" in output.out
        assert f"Catalog failures: {int(status == 'PARTIAL')}" in output.out
        assert "Facts: 1" in output.out
        assert "Fact dimensions: 1" in output.out
        assert "Rejected facts: 0" in output.out
        for name in (
            "facts",
            "fact_dimensions",
            "rejected_facts",
            "active_filings",
            "catalog_failures",
        ):
            assert f"silver.{name}" in output.out
