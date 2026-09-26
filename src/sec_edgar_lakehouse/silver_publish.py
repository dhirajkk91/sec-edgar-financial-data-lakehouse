"""Extract and publish one local Bronze filing as a Silver version."""

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from sec_edgar_lakehouse.silver_extraction import extract_filing_facts
from sec_edgar_lakehouse.silver_publication import (
    _contained,
    _read_active,
    publish_silver_extraction,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Publish one filing and print its version and active Parquet paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bronze_filing_directory", type=Path)
    parser.add_argument("bronze_manifest_path", type=Path)
    parser.add_argument("--silver-directory", required=True, type=Path)
    parser.add_argument("--run-id")
    arguments = parser.parse_args(argv)
    run_id = arguments.run_id if arguments.run_id is not None else uuid4().hex
    extraction = extract_filing_facts(
        arguments.bronze_filing_directory, arguments.bronze_manifest_path
    )
    result = publish_silver_extraction(
        extraction,
        bronze_manifest_path=arguments.bronze_manifest_path,
        silver_directory=arguments.silver_directory,
        processing_run_id=run_id,
    )
    print(f"Publication outcome: {result.outcome}")
    print(f"Silver status: {result.silver_status}")
    print(f"Version is active: {result.active}")
    print(f"active.json: {result.active_path}")
    print(f"Version directory: {result.version_path}")
    print(f"publication.json: {result.publication_path}")
    print(f"Run record: {result.run_path}")
    print(
        f"facts={extraction.accepted_count} dimensions={len(extraction.dimensions)} "
        f"rejected={extraction.rejected_count}"
    )
    # A skipped older version isn't necessarily the one the pointer selects.
    filing = result.active_path.parent
    root = filing.parent.parent
    relative = _read_active(filing, extraction, root=root)
    active_version = (
        _contained(filing, relative, root=root) if relative is not None else None
    )
    for filename in (
        "facts.parquet",
        "fact_dimensions.parquet",
        "rejected_facts.parquet",
    ):
        path = (
            active_version / filename if active_version is not None else "unavailable"
        )
        print(f"Active {filename}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
