from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from taxi_contracts import (
    CONTRACT_VERSION,
    MAX_LOCATION_ID,
    MIN_LOCATION_ID,
    PredictionResponse,
    TripFeatures,
)

VALID: dict[str, Any] = {
    "pickup_location_id": 161,
    "dropoff_location_id": 237,
    "trip_distance": 2.4,
    "pickup_datetime": "2025-01-15T08:30:00",
}


def test_valid_payload_parses() -> None:
    features = TripFeatures.model_validate(VALID)
    assert features.pickup_datetime == datetime(2025, 1, 15, 8, 30)  # noqa: DTZ001


def test_valid_json_payload_parses() -> None:
    """The API path: raw JSON, not a Python dict."""
    body = (
        '{"pickup_location_id": 161, "dropoff_location_id": 237, '
        '"trip_distance": 2, "pickup_datetime": "2025-01-15T08:30:00"}'
    )
    assert TripFeatures.model_validate_json(body).trip_distance == 2.0


@pytest.mark.parametrize("location_id", [MIN_LOCATION_ID, MAX_LOCATION_ID])
def test_location_id_bounds_are_inclusive(location_id: int) -> None:
    TripFeatures.model_validate({**VALID, "pickup_location_id": location_id})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pickup_location_id", 0),
        ("dropoff_location_id", MAX_LOCATION_ID + 1),
        # strict: no lax coercion of IDs
        ("pickup_location_id", "161"),
        ("pickup_location_id", 161.0),
        ("pickup_location_id", True),
        ("trip_distance", 0),
        ("trip_distance", -1.0),
        ("trip_distance", 101),
        ("trip_distance", float("nan")),
        ("trip_distance", float("inf")),
        ("pickup_datetime", "not-a-date"),
        ("pickup_datetime", "2008-12-31T23:59:59"),
        # offsets and epochs are ambiguous for a model trained on NYC wall-clock time
        ("pickup_datetime", "2025-01-15T08:30:00Z"),
        ("pickup_datetime", "2025-01-15T08:30:00-05:00"),
        ("pickup_datetime", 1736929800),
    ],
)
def test_invalid_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TripFeatures.model_validate({**VALID, field: value})


def test_unknown_fields_are_rejected() -> None:
    """Typos in client payloads must fail loudly, not be silently ignored."""
    with pytest.raises(ValidationError, match="extra"):
        TripFeatures.model_validate({**VALID, "dropoff_datetime": "2025-01-15T09:00:00"})


def test_missing_field_is_rejected() -> None:
    payload = {k: v for k, v in VALID.items() if k != "trip_distance"}
    with pytest.raises(ValidationError):
        TripFeatures.model_validate(payload)


def test_features_are_immutable() -> None:
    features = TripFeatures.model_validate(VALID)
    with pytest.raises(ValidationError):
        features.trip_distance = 10.0  # type: ignore[misc]


def test_response_carries_contract_version() -> None:
    response = PredictionResponse(
        predicted_duration_minutes=12.5, model_name="m", model_version="1"
    )
    assert response.contract_version == CONTRACT_VERSION


@pytest.mark.parametrize("minutes", [-1.0, float("nan"), float("inf")])
def test_response_rejects_invalid_duration(minutes: float) -> None:
    with pytest.raises(ValidationError):
        PredictionResponse(predicted_duration_minutes=minutes, model_name="m", model_version="1")
