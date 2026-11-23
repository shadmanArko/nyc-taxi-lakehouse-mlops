"""Serving a real registry model, and hot reload when the alias moves."""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from mlflow.models import infer_signature
from sklearn.linear_model import LinearRegression

from taxi_api.config import Settings
from taxi_api.main import create_app
from taxi_api.model_manager import ModelManager
from taxi_api.predictor import ModelRef, Predictor
from taxi_contracts import TripFeatures
from taxi_features import build_pipeline, canonical_inputs

PAYLOAD: dict[str, Any] = {
    "pickup_location_id": 161,
    "dropoff_location_id": 237,
    "trip_distance": 2.4,
    "pickup_datetime": "2025-01-15T08:30:00",
}
NAME = "taxi-trip-duration"


# --- ModelRef / ModelManager (no MLflow) ----------------------------------------


@pytest.mark.parametrize(
    "bad", ["taxi@champion", "models:/taxi", "models:/taxi/3", "runs:/x/model"]
)
def test_model_uri_must_be_an_alias(bad: str) -> None:
    with pytest.raises(ValueError, match="models:/<name>@<alias>"):
        ModelRef.parse(bad)


class Versioned:
    def __init__(self, version: str) -> None:
        self.model_name = NAME
        self.model_version = version

    def predict(self, features: TripFeatures) -> float:
        return float(self.model_version)


def test_manager_loads_only_when_the_alias_moves() -> None:
    alias = {"version": "1"}
    loads: list[str] = []

    def load(_: ModelRef, version: str) -> Predictor:
        loads.append(version)
        return Versioned(version)

    manager = ModelManager(ModelRef(NAME, "champion"), lambda _: alias["version"], load)
    assert manager.refresh() is True
    assert manager.refresh() is False  # unchanged alias: no download
    alias["version"] = "2"
    assert manager.refresh() is True
    assert loads == ["1", "2"]
    assert manager.current is not None
    assert manager.current.model_version == "2"


def test_failed_load_keeps_serving_the_old_model() -> None:
    alias = {"version": "1"}

    def load(_: ModelRef, version: str) -> Predictor:
        if version == "2":
            raise OSError("artifact download failed")
        return Versioned(version)

    manager = ModelManager(ModelRef(NAME, "champion"), lambda _: alias["version"], load)
    manager.refresh()
    alias["version"] = "2"
    with pytest.raises(OSError, match="download failed"):
        manager.refresh()
    assert manager.current is not None
    assert manager.current.model_version == "1"


def test_api_goes_ready_and_hot_swaps_without_restart() -> None:
    alias = {"version": "1"}
    manager = ModelManager(
        ModelRef(NAME, "champion"), lambda _: alias["version"], lambda _, v: Versioned(v)
    )
    settings = Settings(model_refresh_seconds=5)
    with TestClient(create_app(settings=settings, manager=manager)) as client:
        _wait_for(lambda: client.get("/ready").status_code == 200)
        assert client.post("/predict", json=PAYLOAD).json()["model_version"] == "1"
        alias["version"] = "2"
        manager.refresh()  # what the background loop does every refresh interval
        client.app.state.predictor = manager.current  # type: ignore[attr-defined]
        assert client.post("/predict", json=PAYLOAD).json()["model_version"] == "2"


def test_api_stays_up_and_not_ready_when_the_registry_is_down() -> None:
    def broken(_: ModelRef) -> str:
        raise ConnectionError("MLflow unreachable")

    manager = ModelManager(ModelRef(NAME, "champion"), broken, lambda _, v: Versioned(v))
    with TestClient(create_app(settings=Settings(), manager=manager)) as client:
        time.sleep(0.2)
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503


def _wait_for(condition: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    msg = "condition not met in time"
    raise AssertionError(msg)


# --- A real MLflow registry (SQLite) --------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> Iterator[None]:
    previous = mlflow.get_tracking_uri()
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    yield
    mlflow.set_tracking_uri(previous)


def _register(slope: float) -> str:
    rng = np.random.default_rng(0)
    frame = pd.DataFrame(
        {
            "pickup_location_id": rng.choice([161, 237, 132], 200),
            "dropoff_location_id": rng.choice([161, 237, 138], 200),
            "trip_distance": rng.uniform(0.5, 15, 200),
            "pickup_datetime": pd.Timestamp("2025-01-15 08:00")
            + pd.to_timedelta(rng.integers(0, 600, 200), "min"),
        }
    )
    x = canonical_inputs(frame)
    pipe = build_pipeline(LinearRegression()).fit(x, slope * x["trip_distance"] + 2)
    with mlflow.start_run():
        mlflow.sklearn.log_model(
            pipe,
            name="model",
            signature=infer_signature(x.head(3), pipe.predict(x.head(3))),
            skops_trusted_types=[
                "sklearn.model_selection._split.KFold",
                "taxi_features.pipeline.TripFeatureBuilder",
            ],
            registered_model_name=NAME,
        )
    client = mlflow.MlflowClient()
    version = max(int(v.version) for v in client.search_model_versions(f"name='{NAME}'"))
    client.set_registered_model_alias(NAME, "champion", str(version))
    return str(version)


def test_serves_the_registry_champion_end_to_end(registry: None) -> None:
    _register(slope=3.0)
    manager = ModelManager(ModelRef.parse(f"models:/{NAME}@champion"))
    with TestClient(create_app(settings=Settings(), manager=manager)) as client:
        _wait_for(lambda: client.get("/ready").status_code == 200, timeout=30)
        body = client.post("/predict", json=PAYLOAD).json()
    assert body["model_name"] == NAME
    assert body["model_version"] == "1"
    assert body["predicted_duration_minutes"] == pytest.approx(3.0 * 2.4 + 2, abs=1e-3)


def test_promotion_is_picked_up_by_refresh(registry: None) -> None:
    _register(slope=3.0)
    manager = ModelManager(ModelRef.parse(f"models:/{NAME}@champion"))
    manager.refresh()
    assert manager.current is not None
    first = manager.current.predict(TripFeatures.model_validate(PAYLOAD))
    _register(slope=4.0)  # a new champion is promoted
    assert manager.refresh() is True
    assert manager.current.model_version == "2"
    assert manager.current.predict(TripFeatures.model_validate(PAYLOAD)) > first


# --- Local model cache (scale-to-zero cold starts) ------------------------------


def test_cold_start_serves_from_cache_while_registry_is_down(
    registry: None,
    tmp_path: Path,
) -> None:
    from taxi_api.predictor import ModelCache

    _register(slope=3.0)
    cache = ModelCache(tmp_path / "models")
    first = ModelManager(ModelRef.parse(f"models:/{NAME}@champion"), cache=cache)
    assert first.refresh() is True
    assert cache.current_version(NAME) == "1"

    def registry_down(_: ModelRef) -> str:
        raise ConnectionError("MLflow is asleep")

    # A brand-new process (container just woke up) with MLflow unreachable:
    woken = ModelManager(ModelRef.parse(f"models:/{NAME}@champion"), registry_down, cache=cache)
    assert woken.warm_start() is True
    assert woken.current is not None
    assert woken.current.model_version == "1"
    minutes = woken.current.predict(TripFeatures.model_validate(PAYLOAD))
    assert minutes == pytest.approx(3.0 * 2.4 + 2, abs=1e-3)
    with pytest.raises(ConnectionError):
        woken.refresh()  # registry still down: keeps serving the cached model
    assert woken.current.model_version == "1"


def test_cache_keeps_only_the_newest_two_versions(
    registry: None,
    tmp_path: Path,
) -> None:
    from taxi_api.predictor import ModelCache

    cache = ModelCache(tmp_path / "models")
    manager = ModelManager(ModelRef.parse(f"models:/{NAME}@champion"), cache=cache)
    for slope in (3.0, 4.0, 5.0):
        _register(slope=slope)
        manager.refresh()
    kept = sorted(p.name for p in (tmp_path / "models" / NAME).iterdir() if p.is_dir())
    assert kept == ["2", "3"]
    assert cache.current_version(NAME) == "3"


def test_no_cache_means_no_warm_start() -> None:
    manager = ModelManager(ModelRef(NAME, "champion"), lambda _: "1", lambda _, v: Versioned(v))
    assert manager.warm_start() is False


def test_cached_model_is_loaded_before_the_first_request(
    registry: None,
    tmp_path: Path,
) -> None:
    """Scale-to-zero releases traffic on 'healthy': no 503 window right after a wake."""
    from taxi_api.predictor import ModelCache

    _register(slope=3.0)
    cache = ModelCache(tmp_path / "models")
    ModelManager(ModelRef.parse(f"models:/{NAME}@champion"), cache=cache).refresh()

    def registry_down(_: ModelRef) -> str:
        raise ConnectionError("MLflow is asleep")

    woken = ModelManager(ModelRef.parse(f"models:/{NAME}@champion"), registry_down, cache=cache)
    with TestClient(create_app(settings=Settings(), manager=woken)) as client:
        # The very first request, with no waiting: the model must already be there.
        assert client.post("/predict", json=PAYLOAD).status_code == 200
        assert client.get("/ready").status_code == 200
