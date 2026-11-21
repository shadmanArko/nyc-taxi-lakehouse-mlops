"""Run the whole medallion flow locally on DuckDB, with the production logic.

bronze (raw parquet + lineage) -> silver (lakehouse_rules SQL + DROP_RULES, exactly as
the Lakeflow pipeline applies them) -> written to a DuckDB file that dbt's ``ci``
target then reads as its silver source to build and test gold.

    uv run python tools/local_lakehouse.py data/raw/tlc/yellow/*.parquet
    cd transform/dbt && DBT_DUCKDB_PATH=../../target/local_lakehouse.duckdb \
        uv run dbt build --profiles-dir . --target ci --exclude resource_type:seed \
        --vars '{min_gold_rows: 3000000}'

A development tool: production runs on Databricks.
"""

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parents[1] / "databricks/src/taxi_pipeline"))
from lakehouse_rules import DROP_RULES, silver_select_sql

DEFAULT_DB = Path(__file__).parents[1] / "target/local_lakehouse.duckdb"


def build(files: list[Path], db: Path) -> dict[str, int]:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    paths = ", ".join(f"'{f.resolve().as_posix()}'" for f in files)
    con.execute(
        f"""CREATE OR REPLACE VIEW bronze_yellow_trips AS
            SELECT *, filename AS _source_file, now() AS _ingested_at
            FROM read_parquet([{paths}], filename = true, union_by_name = true)"""
    )
    predicate = " AND ".join(f"coalesce(({r.expression}), false)" for r in DROP_RULES.values())
    con.execute(
        f"""CREATE OR REPLACE TABLE main.yellow_trips AS
            SELECT * FROM ({silver_select_sql("bronze_yellow_trips", "duckdb")})
            WHERE {predicate}"""
    )
    counts = {
        "bronze": con.execute("SELECT count(*) FROM bronze_yellow_trips").fetchone()[0],  # type: ignore[index]
        "silver": con.execute("SELECT count(*) FROM main.yellow_trips").fetchone()[0],  # type: ignore[index]
    }
    con.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    counts = build(args.files, args.db)
    print(f"bronze={counts['bronze']:,} silver={counts['silver']:,} -> {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
