"""Dataset contracts: what each data layer promises its consumers.

* ``RAW_YELLOW_REQUIRED`` - columns the ingestion job demands from a TLC monthly file
  before it may land in the lakehouse. Checked by *type family*, not exact width:
  the TLC has changed int32/int64 and string/large_string between months.
* ``GOLD_TRIP_FEATURES`` - the gold table the training job reads. The dbt model
  ``trip_features`` enforces the same columns and types (a test keeps them equal).

Verified against ``yellow_tripdata_2025-01.parquet`` (see notebooks/01_eda_*).
"""

from enum import StrEnum
from typing import Final


class TypeFamily(StrEnum):
    INTEGER = "integer"
    FLOAT = "float"
    TIMESTAMP = "timestamp"
    STRING = "string"


RAW_YELLOW_REQUIRED: Final[dict[str, TypeFamily]] = {
    "VendorID": TypeFamily.INTEGER,
    "tpep_pickup_datetime": TypeFamily.TIMESTAMP,
    "tpep_dropoff_datetime": TypeFamily.TIMESTAMP,
    "passenger_count": TypeFamily.INTEGER,  # 15% NULL in 2025-01 (metadata-missing cohort)
    "trip_distance": TypeFamily.FLOAT,
    "RatecodeID": TypeFamily.INTEGER,
    "store_and_fwd_flag": TypeFamily.STRING,
    "PULocationID": TypeFamily.INTEGER,
    "DOLocationID": TypeFamily.INTEGER,
    "payment_type": TypeFamily.INTEGER,
    "fare_amount": TypeFamily.FLOAT,
    "total_amount": TypeFamily.FLOAT,
}

# Gold feature table, one row per cleaned trip. SQL types as Databricks/DuckDB spell them.
GOLD_TRIP_FEATURES_TABLE: Final = "trip_features"
GOLD_TRIP_FEATURES: Final[dict[str, str]] = {
    "trip_id": "string",  # deterministic hash of the trip key
    "source_month": "date",  # first day of the TLC file's month
    "pickup_datetime": "timestamp",  # NYC local wall-clock time
    "pickup_date": "date",
    "pickup_hour": "int",  # 0-23
    "pickup_day_of_week": "int",  # ISO: 1 = Monday ... 7 = Sunday
    "is_weekend": "boolean",
    "pickup_location_id": "int",
    "dropoff_location_id": "int",
    "trip_distance": "double",  # miles
    "is_metadata_missing": "boolean",
    "duration_min": "double",  # TARGET: never a feature
}
GOLD_TARGET: Final = "duration_min"
