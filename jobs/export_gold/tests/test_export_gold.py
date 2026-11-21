import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from taxi_contracts import CONTRACT_VERSION, GOLD_TRIP_FEATURES
from taxi_export_gold import cli
from taxi_export_gold.core import (
    ExportError,
    build_query,
    check_identifier,
    fetch_month,
    publish,
    validate,
    write_parquet,
)

PREFIX = "snapshots/gold/trip_features"


def gold_table(rows: int = 3, **overrides: pa.Array) -> pa.Table:
    data: dict[str, pa.Array] = {
        "trip_id": pa.array([f"id{i}" for i in reversed(range(rows))]),
        "source_month": pa.array([date(2025, 1, 1)] * rows),
        "pickup_datetime": pa.array([datetime(2025, 1, 2, 8, i) for i in range(rows)]),  # noqa: DTZ001
        "pickup_date": pa.array([date(2025, 1, 2)] * rows),
        "pickup_hour": pa.array([8] * rows, pa.int32()),
        "pickup_day_of_week": pa.array([4] * rows, pa.int32()),
        "is_weekend": pa.array([False] * rows),
        "pickup_location_id": pa.array([161] * rows, pa.int32()),
        "dropoff_location_id": pa.array([237] * rows, pa.int32()),
        "trip_distance": pa.array([2.5] * rows),
        "is_metadata_missing": pa.array([False] * rows),
        "duration_min": pa.array([12.0] * rows),
    }
    data.update(overrides)
    return pa.table(data)


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0

    def head_object(self, *, Bucket: str, Key: str) -> Any:  # noqa: N803
        return {"ContentLength": len(self.objects[f"{Bucket}/{Key}"])}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_: Any) -> Any:  # noqa: N803
        self.puts += 1
        self.objects[f"{Bucket}/{Key}"] = Body


class FakeCursor:
    def __init__(self, table: pa.Table) -> None:
        self.table = table
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def execute(self, operation: str, parameters: dict[str, Any] | None = None) -> None:
        self.calls.append((operation, parameters))

    def fetchall_arrow(self) -> pa.Table:
        return self.table


# --- query safety ---------------------------------------------------------------


def test_query_selects_contract_columns_and_binds_the_month() -> None:
    cursor = FakeCursor(gold_table())
    fetch_month(cursor, "workspace", "taxi_gold", "2025-01")
    ((sql, params),) = cursor.calls
    assert sql.startswith("SELECT " + ", ".join(GOLD_TRIP_FEATURES) + " FROM workspace.taxi_gold.")
    assert params == {"month_start": "2025-01-01"}
    assert "2025" not in sql  # the month is a bound parameter, never interpolated


@pytest.mark.parametrize("bad", ["taxi_gold; DROP TABLE x", "a.b", "", "1abc", "x-y"])
def test_identifiers_are_validated(bad: str) -> None:
    with pytest.raises(ValueError, match="plain identifier"):
        check_identifier(bad, "schema")
    with pytest.raises(ValueError, match="plain identifier"):
        build_query("workspace", bad)


# --- contract -------------------------------------------------------------------


def test_valid_table_passes() -> None:
    validate(gold_table())


@pytest.mark.parametrize(
    ("table", "message"),
    [
        (gold_table().drop_columns(["duration_min"]), "columns"),
        (gold_table(pickup_hour=pa.array(["8", "8", "8"])), "pickup_hour is string"),
        (gold_table(duration_min=pa.array([1.0, None, 2.0])), "duration_min has NULLs"),
        (gold_table(trip_id=pa.array(["a", "a", "b"])), "not unique"),
        (gold_table(rows=0), "no rows"),
    ],
)
def test_contract_violations_are_rejected(table: pa.Table, message: str) -> None:
    with pytest.raises(ExportError, match=message):
        validate(table)


# --- snapshot -------------------------------------------------------------------


def test_parquet_is_deterministic(tmp_path: Path) -> None:
    """Same data, any row order -> same bytes -> same hash (content addressing works)."""
    table = gold_table(rows=5)
    shuffled = table.take([3, 0, 4, 1, 2])
    assert write_parquet(table, tmp_path / "a.parquet") == write_parquet(
        shuffled, tmp_path / "b.parquet"
    )


def test_publish_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = FakeStore()
    local = tmp_path / "x.parquet"
    sha = write_parquet(gold_table(), local)
    first = publish(local, sha, 3, "2025-01", "w.s.trip_features", store, "b", PREFIX)
    base = f"b/{PREFIX}/source_month=2025-01"
    assert set(store.objects) == {
        f"{base}/{sha}.parquet",
        f"{base}/{sha}.manifest.json",
        f"{base}/LATEST",
    }
    manifest = json.loads(store.objects[f"{base}/{sha}.manifest.json"])
    assert manifest["contract_version"] == CONTRACT_VERSION
    assert manifest["columns"] == GOLD_TRIP_FEATURES
    assert store.objects[f"{base}/LATEST"] == f"{sha}\n".encode()

    puts_before = store.puts
    second = publish(local, sha, 3, "2025-01", "w.s.trip_features", store, "b", PREFIX)
    assert store.puts == puts_before + 1  # only LATEST is rewritten
    assert second.object_key == first.object_key


def test_unexpected_storage_errors_propagate(tmp_path: Path) -> None:
    class Broken(FakeStore):
        def head_object(self, *, Bucket: str, Key: str) -> Any:  # noqa: N803
            raise PermissionError("403")

    local = tmp_path / "x.parquet"
    sha = write_parquet(gold_table(), local)
    with pytest.raises(PermissionError):
        publish(local, sha, 3, "2025-01", "t", Broken(), "b", PREFIX)


def test_boto_style_not_found_counts_as_missing(tmp_path: Path) -> None:
    class NotFoundError(Exception):
        response = {"Error": {"Code": "404"}}  # noqa: RUF012

    class BotoLike(FakeStore):
        def head_object(self, *, Bucket: str, Key: str) -> Any:  # noqa: N803
            raise NotFoundError

    local = tmp_path / "x.parquet"
    sha = write_parquet(gold_table(), local)
    store = BotoLike()
    publish(local, sha, 3, "2025-01", "t", store, "b", PREFIX)
    assert store.puts == 3


# --- cli ------------------------------------------------------------------------


def test_cli_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    store = FakeStore()
    code = cli.main(
        ["--month", "2025-01", "--schema", "taxi_gold", "--bucket", "b"],
        cursor=FakeCursor(gold_table()),
        store=store,
    )
    assert code == 0
    assert "rows=3" in capsys.readouterr().out
    assert len(store.objects) == 3


def test_cli_refuses_to_publish_a_bad_snapshot() -> None:
    store = FakeStore()
    code = cli.main(
        ["--month", "2025-01", "--schema", "taxi_gold"],
        cursor=FakeCursor(gold_table(rows=0)),
        store=store,
    )
    assert code == 1
    assert store.objects == {}


def test_cli_rejects_bad_schema_name() -> None:
    with pytest.raises(ValueError, match="plain identifier"):
        cli.main(
            ["--month", "2025-01", "--schema", "x;y"],
            cursor=FakeCursor(gold_table()),
            store=FakeStore(),
        )
