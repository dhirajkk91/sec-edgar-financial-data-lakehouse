"""Publish three fictional filings through the normal Silver and metadata APIs."""

import hashlib
import json
from pathlib import Path
from typing import Any

from sec_edgar_lakehouse import (
    extract_filing_facts,
    load_company_profile,
    load_filing_fiscal_metadata,
    load_filing_metadata,
    publish_silver_extraction,
    refresh_silver_catalog,
)

CIK = "0000000042"
ACCESSIONS = ("0000000042-25-000002", "0000000042-25-000003", "0000000042-26-000001")
FIXTURES = Path(__file__).resolve().parents[2] / "tests/ci/fixtures"


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def build_synthetic_catalog(work_dir: Path) -> Path:
    """Save local source evidence, publish verified versions, and load both metadata tables."""
    work_dir.mkdir(parents=True, exist_ok=True)
    if any(work_dir.iterdir()):
        raise ValueError("Synthetic work directory must be empty")
    filings = (
        ("q2.xml", ACCESSIONS[0], "10-Q", "2025-08-01", "2025-06-30"),
        ("q3.xml", ACCESSIONS[1], "10-Q", "2025-11-01", "2025-09-30"),
        ("annual.xml", ACCESSIONS[2], "10-K", "2026-02-01", "2025-12-31"),
    )
    inputs = []
    for filename, accession, _, _, _ in filings:
        directory = work_dir / "bronze" / f"cik={CIK}" / f"accession={accession}"
        for folder in ("metadata", "sec-derived", "manifests"):
            (directory / folder).mkdir(parents=True)
        discovery = {
            "cik": CIK,
            "accession_number": accession,
            "inventory": [
                {
                    "section": "data-file",
                    "document_name": filename,
                    "description": "EXTRACTED XBRL INSTANCE DOCUMENT",
                    "sequence": "1",
                    "document_type": "XML",
                    "required_for_source": False,
                }
            ],
        }
        write_json(directory / "metadata/discovery.json", discovery)
        (directory / "sec-derived" / filename).write_bytes(
            (FIXTURES / filename).read_bytes()
        )
        files = []
        for section, name, path in (
            ("metadata", "discovery.json", directory / "metadata/discovery.json"),
            ("data-file", filename, directory / "sec-derived" / filename),
        ):
            content = path.read_bytes()
            files.append(
                {
                    "section": section,
                    "document_name": name,
                    "status": "VERIFIED",
                    "required_for_source": section == "metadata",
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        manifest = directory / "manifests/run_id=synthetic.json"
        write_json(
            manifest,
            {
                "run_id": "synthetic",
                "cik": CIK,
                "accession_number": accession,
                "status": "COMPLETE",
                "source_complete": True,
                "parser_ready": True,
                "files": files,
            },
        )
        extraction = extract_filing_facts(directory, manifest)
        assert extraction.status == "COMPLETE" and extraction.rejected_count == 0
        publication = publish_silver_extraction(
            extraction,
            bronze_manifest_path=manifest,
            silver_directory=work_dir / "silver",
            processing_run_id="synthetic",
        )
        assert publication.outcome == "PUBLISHED" and publication.active
        inputs.append((directory, manifest))
    database = work_dir / "synthetic.duckdb"
    catalog = refresh_silver_catalog(
        silver_directory=work_dir / "silver", database_path=database
    )
    assert catalog.status == "COMPLETE" and catalog.active_filing_count == 3
    run_dir = work_dir / "company-runs" / f"cik={CIK}" / "run_id=synthetic"
    run_dir.mkdir(parents=True)
    write_json(
        run_dir / "submissions.json",
        {
            "cik": 42,
            "name": "Fictional CI Company (synthetic)",
            "tickers": [],
            "exchanges": [],
            "sic": "7372",
            "sicDescription": "Services-Prepackaged Software",
            "ownerOrg": "06 Technology",
            "fiscalYearEnd": "1231",
            "filings": {
                "files": [],
                "recent": {
                    "accessionNumber": [row[1] for row in filings],
                    "form": [row[2] for row in filings],
                    "filingDate": [row[3] for row in filings],
                    "reportDate": [row[4] for row in filings],
                    "primaryDocument": [
                        row[0].replace(".xml", ".htm") for row in filings
                    ],
                },
            },
        },
    )
    content = (run_dir / "submissions.json").read_bytes()
    # The loader checks this canonical URL as provenance. No request is made.
    write_json(
        run_dir / "run.json",
        {
            "schema_version": "1",
            "run_id": "synthetic",
            "cik": CIK,
            "submissions_evidence": {
                "path": "submissions.json",
                "url": f"https://data.sec.gov/submissions/CIK{CIK}.json",
                "retrieved_at": "2026-02-02T00:00:00+00:00",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        },
    )
    metadata = load_filing_metadata(
        run_record_path=run_dir / "run.json", database_path=database
    )
    assert metadata.status == "COMPLETE" and metadata.inserted_count == 3
    profile = load_company_profile(
        run_record_path=run_dir / "run.json", database_path=database
    )
    assert profile.outcome == "INSERTED"
    for directory, manifest in inputs:
        fiscal = load_filing_fiscal_metadata(
            directory, manifest, database_path=database
        )
        assert fiscal.outcome == "INSERTED" and fiscal.metadata.status == "COMPLETE"
    write_json(
        work_dir / "catalog-summary.json",
        {
            "synthetic": True,
            "cik": CIK,
            "accessions": ACCESSIONS,
            "active_filings": catalog.active_filing_count,
            "facts": catalog.facts_count,
            "dimensions": catalog.fact_dimensions_count,
            "rejected": catalog.rejected_facts_count,
            "filing_metadata": metadata.inserted_count,
            "company_profile": profile.outcome,
            "fiscal_metadata": len(inputs),
        },
    )
    return database
