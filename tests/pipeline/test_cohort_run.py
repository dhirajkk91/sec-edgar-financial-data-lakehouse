"""The cohort plan chooses source-backed fiscal candidates before any filing work."""

import json
from datetime import UTC, date, datetime

from sec_edgar_lakehouse.bronze.company_filings import _parse
from sec_edgar_lakehouse.pipeline.cohort_run import _selection


def test_five_annual_periods_and_quarters_are_selected_by_report_date() -> None:
    rows = []
    number = 1
    for year in range(2020, 2026):
        rows.append((number, f"{year + 1}-02-01", f"{year}-12-31", "10-K"))
        number += 1
        if year > 2020:
            for month in (3, 6, 9):
                rows.append(
                    (
                        number,
                        f"{year}-{month + 1:02}-15",
                        f"{year}-{month:02}-30",
                        "10-Q",
                    )
                )
                number += 1
    rows.append((number, "2025-08-01", "2024-12-31", "10-K/A"))
    content = json.dumps(
        {
            "cik": 42,
            "name": "Synthetic Issuer",
            "tickers": ["TEST"],
            "exchanges": ["NYSE"],
            "filings": {
                "recent": {
                    "accessionNumber": [f"0000000042-25-{row[0]:06}" for row in rows],
                    "filingDate": [row[1] for row in rows],
                    "reportDate": [row[2] for row in rows],
                    "form": [row[3] for row in rows],
                    "primaryDocument": [f"filing-{row[0]}.htm" for row in rows],
                }
            },
        }
    ).encode()
    discovery = _parse(
        content,
        "0000000042",
        "https://data.sec.gov/submissions/CIK0000000042.json",
        datetime.now(UTC),
    )
    plan = _selection(discovery, date(2026, 10, 15))
    assert [year["annual_report_date"] for year in plan["years"]] == [
        f"{year}-12-31" for year in range(2025, 2020, -1)
    ]
    assert len(plan["selected_accessions"]) == 20
    assert all(len(year["quarter_accessions"]) == 3 for year in plan["years"])
    assert plan["plan_gaps"] == []
    assert len(plan["amendments_excluded"]) == 1
    assert plan["amendments_excluded"][0]["form"] == "10-K/A"
