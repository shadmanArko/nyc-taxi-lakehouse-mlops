from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from taxi_api.config import Settings
from taxi_api.main import create_app
from taxi_contracts import CONTRACT_VERSION, TripFeatures

PAYLOAD: dict[str, Any] = {
    "pickup_location_id": 161,
    "dropoff_location_id": 237,
    "trip_distance": 2.4,
    "pickup_datetime": "2025-01-15T08:30:00",
}
SETTINGS = Settings(model_uri=None, log_level="WARNING")


class FakePredictor:
    model_name = "fake-model"
    model_version = "7"

    def __init__(self, minutes: float = 12.5) -> None:
        self.minutes = minutes
        self.calls: list[TripFeatures] = []

    def predict(self, features: TripFeatures) -> float:
        self.calls.append(features)
        return self.minutes


@pytest.fixture
def no_model_client() -> Iterator[TestClient]:
    with TestClient(create_app(settings=SETTINGS)) as client:
        yield client


@pytest.fixture
def fake() -> FakePredictor:
    return FakePredictor()


@pytest.fixture
def client(fake: FakePredictor) -> Iterator[TestClient]:
    with TestClient(create_app(predictor=fake, settings=SETTINGS)) as client:
        yield client


# --- liveness / readiness -------------------------------------------------------


def test_health_is_ok_without_a_model(no_model_client: TestClient) -> None:
    response = no_model_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["contract_version"] == CONTRACT_VERSION


def test_not_ready_without_a_model(no_model_client: TestClient) -> None:
    response = no_model_client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "No model is loaded yet."}


def test_ready_reports_model_lineage(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "model_name": "fake-model",
        "model_version": "7",
    }


def test_endpoints_work_before_lifespan_runs() -> None:
    """Without the context manager, lifespan never runs: must be 503, not a 500."""
    client = TestClient(create_app(settings=SETTINGS))
    assert client.get("/ready").status_code == 503


# --- /predict -------------------------------------------------------------------


def test_predict_without_a_model_is_503_not_a_fake_answer(no_model_client: TestClient) -> None:
    assert no_model_client.post("/predict", json=PAYLOAD).status_code == 503


def test_predict_returns_prediction_and_lineage(client: TestClient, fake: FakePredictor) -> None:
    response = client.post("/predict", json=PAYLOAD)
    assert response.status_code == 200
    assert response.json() == {
        "predicted_duration_minutes": 12.5,
        "model_name": "fake-model",
        "model_version": "7",
        "contract_version": CONTRACT_VERSION,
    }
    assert fake.calls == [TripFeatures.model_validate(PAYLOAD)]


def test_negative_model_output_is_clipped_to_zero() -> None:
    with TestClient(create_app(predictor=FakePredictor(minutes=-3.0), settings=SETTINGS)) as c:
        response = c.post("/predict", json=PAYLOAD)
    assert response.status_code == 200
    assert response.json()["predicted_duration_minutes"] == 0.0


@pytest.mark.parametrize("bad_output", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_model_output_is_a_500_not_a_null(bad_output: float) -> None:
    with TestClient(
        create_app(predictor=FakePredictor(minutes=bad_output), settings=SETTINGS)
    ) as c:
        response = c.post("/predict", json=PAYLOAD)
    assert response.status_code == 500
    assert response.json() == {"detail": "The model produced an invalid prediction."}


# --- client errors are 422, whatever the server state ---------------------------


def test_invalid_payload_is_422_and_never_reaches_the_model(
    client: TestClient, fake: FakePredictor
) -> None:
    response = client.post("/predict", json={**PAYLOAD, "trip_distance": -5})
    assert response.status_code == 422
    assert fake.calls == []


def test_invalid_payload_is_422_even_without_a_model(no_model_client: TestClient) -> None:
    response = no_model_client.post("/predict", json={**PAYLOAD, "pickup_location_id": 0})
    assert response.status_code == 422


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_numbers_are_422_not_500(
    client: TestClient, fake: FakePredictor, token: str
) -> None:
    body = (
        '{"pickup_location_id": 161, "dropoff_location_id": 237, '
        f'"trip_distance": {token}, "pickup_datetime": "2025-01-15T08:30:00"}}'
    )
    response = client.post("/predict", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert fake.calls == []


def test_validation_errors_do_not_echo_the_input(client: TestClient) -> None:
    response = client.post("/predict", json={**PAYLOAD, "trip_distance": -5})
    errors = response.json()["detail"]
    assert errors
    assert all(set(error) <= {"type", "loc", "msg"} for error in errors)


# --- config ---------------------------------------------------------------------


def test_zones_lists_named_zones_without_unknowns(no_model_client: TestClient) -> None:
    response = no_model_client.get("/zones")
    assert response.status_code == 200
    zones = response.json()
    ids = {z["id"] for z in zones}
    assert {1, 132, 138, 161}.issubset(ids)  # EWR, JFK, LaGuardia, Midtown Center
    assert not ids & {264, 265}  # "Unknown" / "Outside of NYC" are not offered
    jfk = next(z for z in zones if z["id"] == 132)
    assert jfk == {
        "id": 132,
        "borough": "Queens",
        "zone": "JFK Airport",
        "service_zone": "Airports",
    }
    assert "max-age" in response.headers["cache-control"]


def test_model_refresh_seconds_is_bounded() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 5"):
        Settings(model_refresh_seconds=1)


def test_log_level_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAXI_API_LOG_LEVEL", "debug")
    assert Settings().log_level == "DEBUG"


def test_repo_sample_request_is_a_valid_contract_payload(client: TestClient) -> None:
    """The assignment's sample-request.json must always match the live contract."""
    import json
    from pathlib import Path

    sample = json.loads((Path(__file__).parents[3] / "sample-request.json").read_text())
    assert client.post("/predict", json=sample).status_code == 200


# --- portal snapshot ------------------------------------------------------------


def test_portal_is_503_until_a_snapshot_is_published(no_model_client: TestClient) -> None:
    assert no_model_client.get("/portal").status_code == 503


def test_portal_serves_and_caches_the_latest_snapshot(tmp_path: Any) -> None:
    from taxi_api.model_manager import ModelManager
    from taxi_api.portal import PortalStore
    from taxi_api.predictor import ModelRef

    published = {"run": ("run-1", {"snapshot_version": 1, "ml": {"status": "ok"}})}
    store = PortalStore(tmp_path, fetch=lambda: published["run"])
    manager = ModelManager(ModelRef("m", "champion"), lambda _: "1", lambda *_: FakePredictor())
    with TestClient(create_app(settings=SETTINGS, manager=manager, portal=store)) as client:
        import time

        deadline = time.monotonic() + 5
        while client.get("/portal").status_code != 200 and time.monotonic() < deadline:
            time.sleep(0.05)
        body = client.get("/portal")
        assert body.json()["ml"] == {"status": "ok"}
        assert "max-age" in body.headers["cache-control"]
    # A new process (cold start, MLflow asleep) serves the cached copy at once.
    cold = PortalStore(tmp_path, fetch=lambda: None)
    assert cold.warm_start() is True
    assert cold.snapshot == {"snapshot_version": 1, "ml": {"status": "ok"}}
    assert cold.refresh() is False
