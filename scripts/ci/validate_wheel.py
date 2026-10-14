"""Build distributions and check a core-only wheel install outside the checkout."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    work_dir = args.work_dir.resolve()
    if work_dir.is_relative_to(root):
        raise ValueError("Use a temporary directory outside the checkout")
    work_dir.mkdir(parents=True, exist_ok=True)
    if any(work_dir.iterdir()):
        raise ValueError("Wheel work directory must be empty")
    environment = os.environ.copy()
    for name in (
        "SEC_USER_AGENT",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "SEC_EDGAR_DUCKDB_PATH",
    ):
        environment.pop(name, None)
    summary = {"status": "FAILED"}
    try:
        with (work_dir / "setup.log").open("w", encoding="utf-8") as log:

            def run(command: list[str]) -> None:
                log.write("$ " + " ".join(command) + "\n")
                log.flush()
                subprocess.run(
                    command,
                    cwd=work_dir,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                    check=True,
                )

            run(
                [
                    "uv",
                    "build",
                    "--project",
                    str(root),
                    "--out-dir",
                    str(work_dir / "dist"),
                ]
            )
            wheels = list((work_dir / "dist").glob("*.whl"))
            assert (
                len(wheels) == 1
                and len(list((work_dir / "dist").glob("*.tar.gz"))) == 1
            )
            with zipfile.ZipFile(wheels[0]) as wheel:
                expected = {
                    path.relative_to(root / "src").as_posix()
                    for path in (root / "src/sec_edgar_lakehouse").rglob("*.py")
                }
                assert expected <= set(wheel.namelist()), (
                    "Wheel is missing relocated modules"
                )
            requirements = work_dir / "runtime-requirements.txt"
            run(
                [
                    "uv",
                    "export",
                    "--project",
                    str(root),
                    "--locked",
                    "--no-default-groups",
                    "--no-emit-project",
                    "--no-header",
                    "--no-annotate",
                    "--output-file",
                    str(requirements),
                ]
            )
            run(["uv", "venv", "--python", sys.executable, str(work_dir / "venv")])
            python = work_dir / "venv/bin/python"
            run(
                [
                    "uv",
                    "pip",
                    "sync",
                    "--python",
                    str(python),
                    "--require-hashes",
                    str(requirements),
                ]
            )
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-deps",
                    str(wheels[0]),
                ]
            )
            probe = work_dir / "wheel_probe.py"
            shutil.copyfile(root / "scripts/ci/wheel_probe.py", probe)
            shutil.copyfile(
                root / "tests/ci/fixtures/gold-reader.json",
                work_dir / "gold-reader.json",
            )
            # -I ignores PYTHONPATH and the checkout; the probe has no project-path imports.
            run(
                [
                    str(python),
                    "-I",
                    str(probe),
                    "--work-dir",
                    str(work_dir),
                    "--checkout",
                    str(root),
                    "--lockfile",
                    str(root / "uv.lock"),
                ]
            )
        summary = json.loads((work_dir / "probe-summary.json").read_text())
        summary["wheel_modules_verified"] = len(expected)
        summary["wheel"] = wheels[0].name
        summary["sdist"] = next((work_dir / "dist").glob("*.tar.gz")).name
        print("Core-only wheel validation: COMPLETE")
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        (work_dir / "wheel-summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
