"""Silver: typed, de-duplicated, quality-checked trips.

The SQL and the rules live in ``lakehouse_rules`` (pure Python, unit-tested locally
with DuckDB on the real data). This file only wires them into Lakeflow: drop rules
become ``expect_all_or_drop`` (dropped counts per rule show in the event log), warn
rules become ``expect_all`` (counted, kept).
"""

from pyspark import pipelines as dp
from pyspark.sql import DataFrame, SparkSession

from lakehouse_rules import DROP_RULES, WARN_RULES, silver_select_sql
from lakehouse_rules.quality import as_expectations

spark = SparkSession.active()
CATALOG = spark.conf.get("taxi.catalog")
BRONZE_SCHEMA = spark.conf.get("taxi.bronze_schema")
SILVER_SCHEMA = spark.conf.get("taxi.silver_schema")


@dp.materialized_view(
    name=f"{CATALOG}.{SILVER_SCHEMA}.yellow_trips",
    comment="Cleaned NYC yellow taxi trips: typed, one row per trip, quality rules applied.",
    table_properties={"quality": "silver"},
)
@dp.expect_all_or_drop(as_expectations(DROP_RULES))
@dp.expect_all(as_expectations(WARN_RULES))
def yellow_trips() -> DataFrame:
    return spark.sql(silver_select_sql(f"{CATALOG}.{BRONZE_SCHEMA}.yellow_trips", "spark"))
