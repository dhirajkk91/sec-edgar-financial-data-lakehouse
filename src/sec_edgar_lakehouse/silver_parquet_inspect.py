"""Print a compact SQL summary of local Silver Parquet files."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import duckdb


def main(argv: Sequence[str] | None = None) -> int:
    """Query three Silver Parquet files without creating a database file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path)
    arguments = parser.parse_args(argv)
    output_directory = arguments.output_directory
    facts_path = output_directory / "facts.parquet"
    dimensions_path = output_directory / "fact_dimensions.parquet"
    rejections_path = output_directory / "rejected_facts.parquet"

    connection = duckdb.connect(database=":memory:")
    try:
        facts_count = _count(connection, facts_path)
        dimensions_count = _count(connection, dimensions_path)
        rejections_count = _count(connection, rejections_path)
        examples = connection.execute(
            """
            SELECT
                facts.concept_namespace,
                facts.concept_local_name,
                facts.value_decimal,
                facts.is_nil,
                facts.period_kind,
                facts.period_start,
                facts.period_end,
                facts.period_instant,
                coalesce(dimensions.dimension_count, 0) AS dimension_count
            FROM read_parquet(?, hive_partitioning=false) AS facts
            LEFT JOIN (
                SELECT source_occurrence_id, count(*) AS dimension_count
                FROM read_parquet(?, hive_partitioning=false)
                GROUP BY source_occurrence_id
            ) AS dimensions USING (source_occurrence_id)
            ORDER BY facts.source_ordinal
            LIMIT 5
            """,
            [str(facts_path), str(dimensions_path)],
        ).fetchall()
    finally:
        connection.close()

    print(
        f"facts={facts_count} dimensions={dimensions_count} rejected={rejections_count}"
    )
    for index, row in enumerate(examples, start=1):
        namespace, local_name, value, is_nil = row[:4]
        period_kind, start, end, instant, dimension_count = row[4:]
        period = str(instant) if period_kind == "INSTANT" else f"{start} to {end}"
        display_value = "nil" if is_nil else str(value)
        print(
            f"{index}. {{{namespace}}}{local_name} value={display_value} "
            f"period={period} dimensions={dimension_count}"
        )
    return 0


def _count(connection: duckdb.DuckDBPyConnection, path: Path) -> int:
    row = connection.execute(
        "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)", [str(path)]
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Could not read row count from {path}")
    return int(row[0])


if __name__ == "__main__":
    raise SystemExit(main())
