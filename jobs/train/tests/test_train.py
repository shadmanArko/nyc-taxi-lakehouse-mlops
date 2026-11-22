import hashlib
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from taxi_contracts import GOLD_TRIP_FEATURES
from taxi_train import models
from taxi_train.data import SnapshotError, load_from_store, load_local, temporal_split
from taxi_train.evaluate import metrics, slice_rmse
from taxi_train.promote import CHAMPION, PREVIOUS, evaluate_gate
from taxi_train.training import CHALLENGER, MODEL_NAME, run

ZONES = [48, 68, 79, 132, 138, 161, 162, 230, 236, 237]


def gold_frame(days: int = 24, per_day: int = 250, seed: int = 0) -> pd.DataFrame:
    """Synthetic gold rows where duration really depends on distance, zone and hour."""
    rng = np.random.default_rng(seed)
    n = days * per_day
    day = np.repeat(np.arange(days), per_day)
    hour = rng.integers(0, 24, n)
    pickup = pd.to_datetime("2025-01-01") + pd.to_timedelta(day, "D") + pd.to_timedelta(hour, "h")
    pu = rng.choice(ZONES, n)
    do = rng.choice(ZONES, n)
    distance = rng.gamma(2.0, 1.5, n).clip(0.1, 60)
    rush = np.isin(hour, [8, 9, 17, 18]).astype(float)
    duration = 4 + 3.2 * distance + 6 * rush + 4 * np.isin(pu, [132, 138]) + rng.normal(0, 2, n)
    frame = pd.DataFrame(
        {
            "trip_id": [f"t{i}" for i in range(n)],
            "source_month": pd.Timestamp("2025-01-01").date(),
            "pickup_datetime": pickup,
            "pickup_date": pickup.date,
            "pickup_hour": hour.astype("int32"),
            "pickup_day_of_week": (pickup.day_of_week + 1).astype("int32"),
            "is_weekend": pickup.day_of_week >= 5,
            "pickup_location_id": pu.astype("int32"),
            "dropoff_location_id": do.astype("int32"),
            "trip_distance": distance,
            "is_metadata_missing": False,
            "duration_min": duration.clip(1, 120),
        }
    )
    return frame[list(GOLD_TRIP_FEATURES)]


def parquet_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), buffer)
    return buffer.getvalue()


class FakeStore:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def get_object(self, *, Bucket: str, Key: str) -> Any:  # noqa: N803
        return {"Body": io.BytesIO(self.objects[f"{Bucket}/{Key}"])}


@pytest.fixture
def tracking(tmp_path: Path) -> Iterator[None]:
    """A throwaway MLflow tracking server + registry backed by SQLite."""
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_registry_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    yield
    mlflow.set_tracking_uri(previous)


# --- data -----------------------------------------------------------------------


def test_temporal_split_is_ordered_and_disjoint() -> None:
    split = temporal_split(gold_frame())
    assert split.train["pickup_date"].max() < split.validation["pickup_date"].min()
    assert split.validation["pickup_date"].max() < split.test["pickup_date"].min()
    assert split.test["pickup_date"].nunique() == 7
    assert len(split.train) + len(split.validation) + len(split.test) == len(gold_frame())


def test_split_needs_enough_days() -> None:
    with pytest.raises(SnapshotError, match="need more than"):
        temporal_split(gold_frame(days=10))


def test_store_snapshot_is_verified_against_its_hash() -> None:
    payload = parquet_bytes(gold_frame(days=15, per_day=10))
    sha = hashlib.sha256(payload).hexdigest()
    base = "b/snapshots/gold/trip_features/source_month=2025-01"
    store = FakeStore({f"{base}/LATEST": f"{sha}\n".encode(), f"{base}/{sha}.parquet": payload})
    snapshot = load_from_store(store, "b", "2025-01")
    assert snapshot.sha256 == sha
    assert snapshot.uri.endswith(f"{sha}.parquet")

    store.objects[f"{base}/{sha}.parquet"] = payload[:-10] + b"tampered!!"
    with pytest.raises(SnapshotError, match="integrity check failed"):
        load_from_store(store, "b", "2025-01")


def test_local_snapshot_rejects_contract_violations(tmp_path: Path) -> None:
    path = tmp_path / "bad.parquet"
    path.write_bytes(parquet_bytes(gold_frame(days=15, per_day=10).drop(columns=["duration_min"])))
    with pytest.raises(SnapshotError, match="missing gold columns"):
        load_local(path, "2025-01")


# --- models + metrics -----------------------------------------------------------


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        models.build("xgboost")


@pytest.mark.parametrize("name", sorted(models.CANDIDATES))
def test_every_candidate_fits_raw_inputs(name: str) -> None:
    frame = gold_frame(days=15, per_day=40)
    x = frame[["pickup_location_id", "dropoff_location_id", "trip_distance", "pickup_datetime"]]
    params = {"n_estimators": 10} if name != "mean_baseline" else {}
    pipe = models.build(name, params).fit(x, frame["duration_min"])
    assert pipe.predict(x.head(3)).shape == (3,)


def test_metrics_and_slices() -> None:
    frame = gold_frame(days=15, per_day=40)
    y = frame["duration_min"]
    perfect = metrics(y, y.to_numpy())
    assert perfect["rmse"] == 0
    assert perfect["r2"] == 1
    assert "airport" in slice_rmse(frame, y, y.to_numpy())


# --- end to end: train -> register -> gate -> promote / reject ------------------


def test_full_lifecycle(tracking: None, tmp_path: Path) -> None:
    path = tmp_path / "gold.parquet"
    path.write_bytes(parquet_bytes(gold_frame()))
    snapshot = load_local(path, "2025-01")

    good = run(snapshot, "lightgbm", tune_trials=2, tune_sample=2_000, register=True)
    assert good.registered_version == "1"
    assert good.test["rmse"] < 5  # the synthetic signal is learnable
    tags = mlflow.get_run(good.run_id).data.tags
    assert tags["snapshot_sha256"] == snapshot.sha256
    assert tags["split"] == "temporal"

    first = evaluate_gate(snapshot)
    assert first.promoted
    client = mlflow.MlflowClient()
    assert str(client.get_model_version_by_alias(MODEL_NAME, CHAMPION).version) == "1"

    worse = run(snapshot, "mean_baseline", register=True)
    assert (
        str(client.get_model_version_by_alias(MODEL_NAME, CHALLENGER).version)
        == worse.registered_version
    )
    second = evaluate_gate(snapshot)
    assert not second.promoted
    assert any("FAIL" in reason for reason in second.reasons)
    assert str(client.get_model_version_by_alias(MODEL_NAME, CHAMPION).version) == "1"

    # A logged model loads with only MLflow and predicts from raw API fields.
    loaded = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@{CHAMPION}")
    request = pd.DataFrame(
        [
            {
                "pickup_location_id": 161,
                "dropoff_location_id": 237,
                "trip_distance": 2.4,
                "pickup_datetime": pd.Timestamp("2025-01-15 08:30"),
            }
        ]
    )
    assert 1 < float(loaded.predict(request)[0]) < 120


def test_promotion_keeps_previous_champion_for_rollback(tracking: None, tmp_path: Path) -> None:
    path = tmp_path / "gold.parquet"
    path.write_bytes(parquet_bytes(gold_frame()))
    snapshot = load_local(path, "2025-01")
    run(snapshot, "random_forest", train_sample=3_000, register=True)
    evaluate_gate(snapshot)
    run(snapshot, "lightgbm", register=True)
    decision = evaluate_gate(snapshot, min_improvement=0.0, max_slice_regression=1.0)
    client = mlflow.MlflowClient()
    if decision.promoted:
        assert str(client.get_model_version_by_alias(MODEL_NAME, PREVIOUS).version) == "1"
        assert str(client.get_model_version_by_alias(MODEL_NAME, CHAMPION).version) == "2"
    else:  # the forest happened to win: champion must be untouched
        assert str(client.get_model_version_by_alias(MODEL_NAME, CHAMPION).version) == "1"
