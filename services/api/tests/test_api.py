from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from taxi_api.config import Settings
from taxi_api.main import create_app, load_predictor
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


def test_load_predictor_returns_none_until_phase_5() -> None:
    assert load_predictor(Settings(model_uri=None)) is None
    assert load_predictor(Settings(model_uri="models:/taxi@champion")) is None


def test_log_level_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAXI_API_LOG_LEVEL", "debug")
    assert Settings().log_level == "DEBUG"
