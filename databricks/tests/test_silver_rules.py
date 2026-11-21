"""Silver SQL + quality rules on synthetic bronze rows, one row per edge case."""

from pathlib import Path

import duckdb
import pytest

from conftest import apply_rules, silver_relation
from lakehouse_rules import DROP_RULES, WARN_RULES, silver_select_sql

SOURCE_FILE = "/Volumes/workspace/taxi_raw/landing/yellow/yellow_tripdata_2025-01.parquet"

# (label, vendor, pickup, dropoff, pax, distance, rate, pu, do, total)
ROWS = [
    ("good", 2, "2025-01-15 08:00:00", "2025-01-15 08:12:00", 1, 2.0, 1, 161, 237, 20.0),
    (
        "good_metadata_missing",
        2,
        "2025-01-15 09:00:00",
        "2025-01-15 09:20:00",
        None,
        3.0,
        None,
        161,
        236,
        25.0,
    ),
    ("charge", 1, "2025-01-16 10:00:00", "2025-01-16 10:15:00", 1, 2.5, 1, 132, 230, 30.0),
    (
        "reversal_of_charge",
        1,
        "2025-01-16 10:00:00",
        "2025-01-16 10:15:00",
        1,
        2.5,
        1,
        132,
        230,
        -30.0,
    ),
    ("void", 1, "2025-01-16 11:00:00", "2025-01-16 11:05:00", 1, 1.0, 1, 132, 230, -5.0),
    (
        "exact_duplicate_a",
        2,
        "2025-01-17 12:00:00",
        "2025-01-17 12:30:00",
        1,
        5.0,
        1,
        100,
        200,
        40.0,
    ),
    (
        "exact_duplicate_b",
        2,
        "2025-01-17 12:00:00",
        "2025-01-17 12:30:00",
        1,
        5.0,
        1,
        100,
        200,
        40.0,
    ),
    (
        "pickup_previous_month",
        2,
        "2024-12-31 23:50:00",
        "2025-01-01 00:10:00",
        1,
        3.0,
        1,
        1,
        2,
        20.0,
    ),
    ("pickup_next_month", 2, "2025-02-01 00:00:10", "2025-02-01 00:20:00", 1, 3.0, 1, 1, 2, 20.0),
    ("duration_under_1_min", 2, "2025-01-18 08:00:00", "2025-01-18 08:00:30", 1, 0.1, 1, 1, 2, 5.0),
    ("duration_over_2_h", 2, "2025-01-18 08:00:00", "2025-01-18 10:30:00", 1, 10.0, 1, 1, 2, 50.0),
    ("negative_duration", 2, "2025-01-18 08:00:00", "2025-01-18 07:50:00", 1, 1.0, 1, 1, 2, 9.0),
    ("zero_distance", 2, "2025-01-18 09:00:00", "2025-01-18 09:10:00", 1, 0.0, 1, 1, 2, 9.0),
    (
        "absurd_distance",
        2,
        "2025-01-18 09:00:00",
        "2025-01-18 09:40:00",
        None,
        276423.57,
        None,
        1,
        2,
        9.0,
    ),
    ("speed_over_80_mph", 2, "2025-01-18 09:00:00", "2025-01-18 09:05:00", 1, 30.0, 1, 1, 2, 60.0),
    ("bad_location", 2, "2025-01-18 09:00:00", "2025-01-18 09:15:00", 1, 2.0, 1, 0, 2, 15.0),
    ("missing_dropoff", 2, "2025-01-18 09:00:00", None, 1, 2.0, 1, 1, 2, 15.0),
    ("unknown_vendor_kept", 9, "2025-01-19 09:00:00", "2025-01-19 09:15:00", 1, 2.0, 1, 1, 2, 15.0),
]
EXPECTED_SILVER = {"good", "good_metadata_missing", "charge", "exact_duplicate", "unknown_vendor"}


@pytest.fixture
def bronze(con: duckdb.DuckDBPyConnection) -> str:
    con.execute(
        """
        CREATE TABLE bronze (
            label VARCHAR, VendorID INTEGER,
            tpep_pickup_datetime TIMESTAMP, tpep_dropoff_datetime TIMESTAMP,
            passenger_count BIGINT, trip_distance DOUBLE, RatecodeID BIGINT,
            store_and_fwd_flag VARCHAR, PULocationID INTEGER, DOLocationID INTEGER,
            payment_type BIGINT, fare_amount DOUBLE, total_amount DOUBLE,
            _source_file VARCHAR, _ingested_at TIMESTAMP
        )
        """
    )
    con.executemany(
        "INSERT INTO bronze VALUES (?, ?, ?, ?, ?, ?, ?, 'N', ?, ?, 1, ?, ?, ?, now())",
        # label..rate, PU, DO, fare (= total), total, source file
        [(*r[:7], r[7], r[8], r[9], r[9], SOURCE_FILE) for r in ROWS],
    )
    return "bronze"


def _labels(con: duckdb.DuckDBPyConnection, sql: str) -> set[str]:
    rows = con.execute(f"SELECT label FROM ({sql})").fetchall()
    return {
        str(label).removesuffix("_a").removesuffix("_b").removesuffix("_kept") for (label,) in rows
    }


def test_silver_keeps_exactly_the_valid_trips(con: duckdb.DuckDBPyConnection, bronze: str) -> None:
    # label is not a silver column; carry it through for assertions only.
    sql = silver_select_sql(bronze, "duckdb").replace(
        "_ingested_at\n    FROM", "_ingested_at, label\n    FROM"
    )
    con.execute(f"CREATE VIEW silver_pre AS {sql}")
    assert _labels(con, apply_rules(con, "silver_pre", DROP_RULES)) == EXPECTED_SILVER


def test_duplicates_and_reversals_collapse_to_one_row(
    con: duckdb.DuckDBPyConnection, bronze: str
) -> None:
    silver = silver_relation(con, bronze)
    counts = con.execute(
        f"""SELECT count(*) FROM {silver}
            WHERE pickup_datetime IN ('2025-01-16 10:00:00', '2025-01-17 12:00:00')"""
    ).fetchone()
    assert counts == (2,)  # charge (reversal gone) + one of the two duplicates


def test_derived_columns(con: duckdb.DuckDBPyConnection, bronze: str) -> None:
    silver = silver_relation(con, bronze)
    row = con.execute(
        f"""SELECT duration_min, source_month, is_metadata_missing FROM {silver}
            WHERE pickup_datetime = '2025-01-15 09:00:00'"""
    ).fetchone()
    assert row is not None
    duration, month, metadata_missing = row
    assert duration == pytest.approx(20.0)
    assert str(month) == "2025-01-01"
    assert metadata_missing is True


def test_every_drop_rule_rejects_at_least_one_fixture_row(
    con: duckdb.DuckDBPyConnection, bronze: str
) -> None:
    """No dead rules: each one must be exercised by the fixture."""
    silver = silver_relation(con, bronze)
    for name, rule in DROP_RULES.items():
        (violations,) = con.execute(
            f"SELECT count(*) FROM {silver} WHERE NOT coalesce(({rule.expression}), false)"
        ).fetchone() or (0,)
        assert violations > 0, f"rule {name} is never violated by the fixture"


def test_warn_rules_flag_but_do_not_drop(con: duckdb.DuckDBPyConnection, bronze: str) -> None:
    silver = silver_relation(con, bronze)
    expr = WARN_RULES["known_vendor"].expression
    (flagged,) = con.execute(f"SELECT count(*) FROM {silver} WHERE NOT ({expr})").fetchone() or (0,)
    assert flagged == 1


def test_rules_cite_evidence() -> None:
    for name, rule in {**DROP_RULES, **WARN_RULES}.items():
        assert rule.evidence.strip(), f"rule {name} has no evidence"


def test_spark_dialect_uses_timestampdiff() -> None:
    assert "timestampdiff(SECOND" in silver_select_sql("b", "spark")


def test_unknown_dialect_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported dialect"):
        silver_select_sql("b", "postgres")  # type: ignore[arg-type]


def test_rule_sql_has_no_division() -> None:
    """Spark ANSI mode raises on divide-by-zero; rules must not divide by a column."""
    assert all("/" not in r.expression for r in DROP_RULES.values())


RAW = Path(__file__).parents[2] / "data/raw/tlc/yellow/yellow_tripdata_2025-01.parquet"


@pytest.mark.skipif(not RAW.exists(), reason="real data not pulled (dvc pull)")
def test_real_january_2025_matches_eda_funnel(con: duckdb.DuckDBPyConnection) -> None:
    """The pipeline's own rules, on the real file, reproduce the EDA notebook's funnel."""
    con.execute(
        f"""CREATE VIEW bronze AS
            SELECT *, filename AS _source_file, now() AS _ingested_at
            FROM read_parquet('{RAW.as_posix()}', filename = true)"""
    )
    silver = silver_relation(con, "bronze")
    (pre_rules,) = con.execute(f"SELECT count(*) FROM {silver}").fetchone() or (0,)
    (kept,) = con.execute(
        f"SELECT count(*) FROM ({apply_rules(con, silver, DROP_RULES)})"
    ).fetchone() or (0,)
    assert pre_rules == 3_412_187  # after dropping reversals/voids and duplicates
    assert kept == 3_312_645  # EDA funnel: speed_plausible
