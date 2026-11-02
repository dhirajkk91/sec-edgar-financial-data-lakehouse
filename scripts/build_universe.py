"""Write config/universe-sp500.json from the public S&P 500 constituents list.

The list has one row per ticker; the pipeline works per SEC filer, so rows are
grouped by CIK (Alphabet's GOOGL and GOOG are one issuer). Run it when the index
changes; the nightly sync picks up the new file on its next run.

    uv run python scripts/build_universe.py [--csv local.csv]
"""

import argparse
import csv
import io
import json
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SOURCE = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)
OUTPUT = Path(__file__).resolve().parents[1] / "config" / "universe-sp500.json"


def build(rows: list[dict[str, str]], as_of: str) -> dict[str, object]:
    issuers: dict[str, dict[str, object]] = {}
    for row in rows:
        cik = row["CIK"].strip().zfill(10)
        if not cik.isdigit() or len(cik) != 10:
            raise ValueError(f"Bad CIK for {row['Symbol']}: {row['CIK']!r}")
        name = re.sub(r"\s*\(Class [A-Z]\)$", "", row["Security"].strip())
        entry = issuers.setdefault(
            cik,
            {"cik": cik, "name": name, "tickers": [], "sector": row["GICS Sector"]},
        )
        tickers = entry["tickers"]
        assert isinstance(tickers, list)
        tickers.append(row["Symbol"].strip().replace(".", "-"))
    for entry in issuers.values():
        entry["tickers"] = sorted(set(entry["tickers"]))  # type: ignore[arg-type]
    return {
        "schema_version": "1",
        "name": "sp500",
        "as_of": as_of,
        "source": SOURCE,
        "note": (
            "S&P 500 constituents grouped by SEC CIK, so share classes are one "
            "issuer. Rebuild with scripts/build_universe.py when the index changes."
        ),
        "issuers": sorted(issuers.values(), key=lambda e: str(e["name"]).casefold()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, help="Read a saved copy instead of the URL")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.csv:
        text = args.csv.read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(SOURCE, timeout=30) as response:
            text = response.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    universe = build(rows, datetime.now(UTC).date().isoformat())
    args.output.write_text(json.dumps(universe, indent=2) + "\n", encoding="utf-8")
    print(f"{args.output}: {len(universe['issuers'])} issuers from {len(rows)} tickers")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
