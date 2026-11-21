"""Bronze: raw TLC files from the landing volume, append-only, as delivered.

Auto Loader picks up each new monthly parquet file exactly once. Columns keep their
source names; we only add lineage (source file, ingestion time). Nothing is dropped
here, so silver can always be rebuilt from bronze.
"""

from pyspark import pipelines as dp
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F  # noqa: N812 - Spark convention

spark = SparkSession.active()
CATALOG = spark.conf.get("taxi.catalog")
BRONZE_SCHEMA = spark.conf.get("taxi.bronze_schema")
LANDING_PATH = spark.conf.get("taxi.landing_path")  # /Volumes/<catalog>/<schema>/<volume>/yellow


@dp.table(
    name=f"{CATALOG}.{BRONZE_SCHEMA}.yellow_trips",
    comment="Raw NYC TLC yellow taxi trips, one row per source record, append-only.",
    table_properties={"quality": "bronze"},
)
def yellow_trips() -> DataFrame:
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .option("cloudFiles.schemaEvolutionMode", "rescue")  # new TLC columns -> _rescued_data
        .load(LANDING_PATH)
        .withColumn("_source_file", F.col("_metadata.file_path"))
        .withColumn("_ingested_at", F.current_timestamp())
    )
