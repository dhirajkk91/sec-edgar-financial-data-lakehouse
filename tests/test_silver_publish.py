import json
from pathlib import Path
from uuid import UUID

import pytest
from test_silver_extraction import fixture, instant_context, usd_unit, xbrl

from sec_edgar_lakehouse.silver_parquet_inspect import main as inspect_main
from sec_edgar_lakehouse.silver_publish import main


@pytest.mark.parametrize("explicit_id", [True, False])
def test_publish_command_paths_counts_and_rerun(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], explicit_id: bool
) -> None:
    directory, manifest = fixture(
        tmp_path / "Bronze with spaces",
        xbrl(
            instant_context(),
            usd_unit(),
            '<us-gaap:Assets contextRef="instant" unitRef="USD">42</us-gaap:Assets>',
        ),
    )
    root = tmp_path / "Silver with spaces"
    args = [str(directory), str(manifest), "--silver-directory", str(root)]
    assert main(args + (["--run-id", "explicit-run"] if explicit_id else [])) == 0
    output = capsys.readouterr().out
    assert "Publication outcome: PUBLISHED" in output
    assert "Silver status: COMPLETE" in output
    assert "Version is active: True" in output
    assert "facts=1 dimensions=0 rejected=0" in output
    paths = dict(line.split(": ", 1) for line in output.splitlines() if ": " in line)
    version = Path(paths["Version directory"])
    for label in ("active.json", "publication.json", "Run record"):
        assert Path(paths[label]).is_file()
    for name in ("facts.parquet", "fact_dimensions.parquet", "rejected_facts.parquet"):
        assert Path(paths[f"Active {name}"]) == version / name
        assert (version / name).is_file()
    run = json.loads(Path(paths["Run record"]).read_text())["processing_run_id"]
    if explicit_id:
        assert run == "explicit-run"
    else:
        assert UUID(run).version == 4
        assert UUID(run).hex == run
    assert inspect_main([str(version)]) == 0
    assert "facts=1 dimensions=0 rejected=0" in capsys.readouterr().out
    assert main(args + ["--run-id", "rerun"]) == 0
    output = capsys.readouterr().out
    assert "Publication outcome: SKIPPED" in output
    assert f"Version directory: {version}" in output
    assert f"Active facts.parquet: {version / 'facts.parquet'}" in output


def test_skipped_inactive_version_prints_current_active_parquet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "silver"
    inputs = []
    for number in (1, 2):
        directory, manifest = fixture(
            tmp_path / f"source {number}",
            xbrl(
                instant_context(),
                usd_unit(),
                f'<us-gaap:Assets contextRef="instant" unitRef="USD">{number}</us-gaap:Assets>',
            ),
        )
        args = [str(directory), str(manifest), "--silver-directory", str(root)]
        inputs.append(args)
        assert main(args + ["--run-id", f"run-{number}"]) == 0
        output = capsys.readouterr().out
    active_paths = [line for line in output.splitlines() if line.startswith("Active ")]
    assert main(inputs[0] + ["--run-id", "old-version-rerun"]) == 0
    output = capsys.readouterr().out
    assert "Publication outcome: SKIPPED" in output
    assert "Version is active: False" in output
    assert all(line in output for line in active_paths)
