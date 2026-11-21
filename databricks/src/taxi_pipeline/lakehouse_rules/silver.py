"""Bronze -> silver transformation as one portable SQL statement.

Renames the TLC's mixed-case columns to snake_case, derives the duration target and
the file's month, drops reversal/void rows and de-duplicates trips. Quality rules
(``quality.py``) are applied on top by the pipeline as expectations.

Only the duration expression differs between engines; everything else is the
common subset of Spark SQL and DuckDB (QUALIFY, regexp_extract, INTERVAL, CAST).
"""

from typing import Final, Literal

Dialect = Literal["spark", "duckdb"]

_DURATION_SECONDS: Final[dict[Dialect, str]] = {
    "spark": "timestampdiff(SECOND, tpep_pickup_datetime, tpep_dropoff_datetime)",
    "duckdb": "date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime)",
}

# One physical trip. EDA s2a: a charge and its reversal share exactly these fields.
TRIP_KEY: Final = (
    "vendor_id",
    "pickup_datetime",
    "dropoff_datetime",
    "pickup_location_id",
    "dropoff_location_id",
    "trip_distance",
)

_TEMPLATE: Final = """
WITH typed AS (
    SELECT
        CAST(VendorID AS INT)               AS vendor_id,
        tpep_pickup_datetime                AS pickup_datetime,
        tpep_dropoff_datetime               AS dropoff_datetime,
        CAST(passenger_count AS INT)        AS passenger_count,
        CAST(trip_distance AS DOUBLE)       AS trip_distance,
        CAST(RatecodeID AS INT)             AS rate_code_id,
        store_and_fwd_flag,
        CAST(PULocationID AS INT)           AS pickup_location_id,
        CAST(DOLocationID AS INT)           AS dropoff_location_id,
        CAST(payment_type AS INT)           AS payment_type,
        CAST(fare_amount AS DOUBLE)         AS fare_amount,
        CAST(total_amount AS DOUBLE)        AS total_amount,
        CAST({duration_seconds} AS DOUBLE) / 60.0 AS duration_min,
        CAST(concat(regexp_extract(_source_file, '([0-9]{{4}}-[0-9]{{2}})[.]parquet$', 1), '-01')
             AS DATE)                       AS source_month,
        passenger_count IS NULL             AS is_metadata_missing,
        _source_file,
        _ingested_at
    FROM {source}
    -- EDA s2a: negative totals are reversals of a charged trip, or voids.
    WHERE total_amount >= 0
)
SELECT *
FROM typed
-- EDA s2a: keep exactly one row per physical trip (latest ingestion wins ties).
QUALIFY row_number() OVER (
    PARTITION BY {trip_key}
    ORDER BY total_amount DESC, _ingested_at DESC
) = 1
"""


def silver_select_sql(source: str, dialect: Dialect) -> str:
    """Render the silver SELECT over ``source`` (a table/view name) for ``dialect``."""
    if dialect not in _DURATION_SECONDS:
        msg = f"unsupported dialect: {dialect!r}"
        raise ValueError(msg)
    return _TEMPLATE.format(
        source=source,
        duration_seconds=_DURATION_SECONDS[dialect],
        trip_key=", ".join(TRIP_KEY),
    )
