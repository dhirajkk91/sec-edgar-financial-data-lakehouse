from pathlib import Path

import pytest
from test_filing_fiscal_metadata import CONCEPTS, document, fact, make_fixture

from sec_edgar_lakehouse.filing_fiscal_metadata_load import main


@pytest.mark.parametrize("partial", [False, True])
def test_load_cli_insert_rerun_and_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], partial: bool
) -> None:
    xml = document(fact(CONCEPTS[0], "bad")) if partial else document()
    paths = make_fixture(tmp_path, xml)
    args = [str(paths[0]), str(paths[1]), "--database", str(paths[2])]
    for outcome in ("INSERTED", "ALREADY_EXISTS"):
        assert main(args) == (2 if partial else 0)
        output = capsys.readouterr().out
        for value in [
            outcome,
            str(paths[2].resolve()),
            "0000320193-24-000123",
            "SHA-256:",
            "Extraction version: 1",
            "Fiscal year: 2024",
            "Occurrences:",
            "Issues:",
        ]:
            assert value in output
        assert "<xbrli:context" not in output
        if partial:
            assert "INVALID_FISCAL_FIELD" in output and "IDs:" in output


def test_load_cli_expected_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = make_fixture(tmp_path, b"<broken")
    args = [str(paths[0]), str(paths[1]), "--database", str(paths[2])]
    assert main(args) == 1 and "FAILED:" in capsys.readouterr().err
    assert main(args[:-1] + [str(tmp_path / "missing.duckdb")]) == 1
    assert not (tmp_path / "missing.duckdb").exists()
