from pathlib import Path

import pytest
from test_filing_fiscal_metadata import CONCEPTS, document, fact, make_fixture

from sec_edgar_lakehouse.filing_fiscal_metadata_inspect import main


@pytest.mark.parametrize("focus,expected", [("FY", 0), ("Q4", 2)])
def test_inspection_status_output_and_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], focus: str, expected: int
) -> None:
    paths = make_fixture(tmp_path, document(focus=focus))
    before = {
        p: p.read_bytes() for p in [*paths[0].rglob("*"), paths[2]] if p.is_file()
    }
    assert main([str(paths[0]), str(paths[1]), "--database", str(paths[2])]) == expected
    output = capsys.readouterr().out
    assert "0000320193-24-000123" in output
    assert "SHA-256:" in output and "Supporting occurrence IDs:" in output
    assert "<xbrli:context" not in output
    if expected == 2:
        assert "INVALID_FISCAL_FIELD" in output
    assert before == {p: p.read_bytes() for p in before}


def test_inspection_conflict_and_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = make_fixture(tmp_path, document(fact(CONCEPTS[0], "2023")))
    args = [str(paths[0]), str(paths[1]), "--database", str(paths[2])]
    assert main(args) == 2
    output = capsys.readouterr().out
    assert "CONFLICTING_FISCAL_FIELD" in output
    assert "fiscal_year_focus: None" in output
    assert main(args[:-1] + [str(tmp_path / "missing.duckdb")]) == 1
    assert "Fiscal metadata failure:" in capsys.readouterr().out
    broken = make_fixture(tmp_path / "other", b"<broken")
    assert main([str(broken[0]), str(broken[1]), "--database", str(broken[2])]) == 1
