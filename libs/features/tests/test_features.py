from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import LinearRegression

from taxi_contracts import TripFeatures
from taxi_features import (
    INPUT_DTYPES,
    RAW_INPUTS,
    TripFeatureBuilder,
    build_pipeline,
    canonical_inputs,
    to_model_input,
)


def raw(n: int = 6) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pickup_location_id": [161, 237, 132, 161, 138, 48][:n],
            "dropoff_location_id": [237, 161, 230, 132, 48, 138][:n],
            "trip_distance": [1.2, 1.5, 17.0, 16.0, 9.5, 9.1][:n],
            "pickup_datetime": pd.to_datetime(
                [
                    "2025-01-13 08:00",  # Monday
                    "2025-01-14 23:30",  # Tuesday
                    "2025-01-18 00:15",  # Saturday
                    "2025-01-19 17:45",  # Sunday
                    "2025-01-15 12:00",
                    "2025-01-16 06:10",
                ][:n]
            ),
        }
    )


def test_raw_inputs_are_exactly_the_api_contract_fields() -> None:
    assert set(RAW_INPUTS) == set(TripFeatures.model_fields)


def test_weekday_is_iso_like_the_gold_table() -> None:
    out = TripFeatureBuilder().fit(raw()).transform(raw())
    assert out["pickup_day_of_week"].tolist()[:4] == [1, 2, 6, 7]
    assert out["is_weekend"].tolist()[:4] == [0, 0, 1, 1]
    assert out["pickup_hour"].tolist()[:4] == [8, 23, 0, 17]


def test_cyclic_hour_makes_midnight_neighbours() -> None:
    frame = raw(2).assign(pickup_datetime=pd.to_datetime(["2025-01-13 23:00", "2025-01-14 00:00"]))
    out = TripFeatureBuilder().fit(frame).transform(frame)
    distance = np.hypot(out["hour_sin"].diff().iloc[1], out["hour_cos"].diff().iloc[1])
    assert distance < 0.3  # one hour apart on the circle, not 23


def test_zone_pair_is_directional() -> None:
    out = TripFeatureBuilder().fit(raw()).transform(raw())
    assert out["zone_pair"].iloc[0] != out["zone_pair"].iloc[1]  # 161->237 vs 237->161


def test_missing_input_column_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing input columns"):
        TripFeatureBuilder().fit(raw().drop(columns=["trip_distance"]))


def test_timezone_aware_times_are_rejected() -> None:
    frame = raw().assign(pickup_datetime=raw()["pickup_datetime"].dt.tz_localize("UTC"))
    with pytest.raises(ValueError, match="naive NYC local time"):
        TripFeatureBuilder().fit(frame).transform(frame)


def test_pipeline_fits_and_predicts_from_raw_fields() -> None:
    frame = pd.concat([raw()] * 10, ignore_index=True)
    y = frame["trip_distance"] * 3 + 5
    pipe = build_pipeline(LinearRegression()).fit(frame, y)
    pred = pipe.predict(frame)
    assert pred.shape == (len(frame),)
    assert np.allclose(pred, y, atol=1e-6)


def test_feature_names_flow_through_the_pipeline() -> None:
    frame = pd.concat([raw()] * 10, ignore_index=True)
    pipe = build_pipeline(DummyRegressor()).fit(frame, np.ones(len(frame)))
    names = list(pipe[:-1].get_feature_names_out())
    assert names[:3] == ["pickup_zone", "dropoff_zone", "zone_pair"]
    assert "hour_sin" in names


def test_canonical_dtypes_match_what_an_api_request_produces() -> None:
    """int32 zone IDs from gold must become int64, like JSON integers from the API."""
    gold_like = raw().astype({"pickup_location_id": "int32", "dropoff_location_id": "int32"})
    out = canonical_inputs(gold_like)
    assert out.dtypes.astype(str).to_dict() == INPUT_DTYPES


def test_utc_labelled_gold_times_keep_their_wall_clock_digits() -> None:
    """Databricks labels gold timestamps UTC; the digits are NYC local and must not shift."""
    labelled = raw().assign(pickup_datetime=raw()["pickup_datetime"].dt.tz_localize("UTC"))
    out = canonical_inputs(labelled)
    assert out["pickup_datetime"].dt.tz is None
    assert out["pickup_datetime"].tolist() == raw()["pickup_datetime"].tolist()


def test_other_timezones_are_refused() -> None:
    shifted = raw().assign(
        pickup_datetime=raw()["pickup_datetime"].dt.tz_localize("America/New_York")
    )
    with pytest.raises(ValueError, match="expected naive NYC wall-clock"):
        canonical_inputs(shifted)


def test_api_payload_converts_to_model_input() -> None:
    payload = TripFeatures(
        pickup_location_id=161,
        dropoff_location_id=237,
        trip_distance=2.4,
        pickup_datetime=datetime(2025, 1, 15, 8, 30),  # noqa: DTZ001 - NYC local by contract
    )
    frame = to_model_input([payload.model_dump()])
    assert list(frame.columns) == list(RAW_INPUTS)
    out = TripFeatureBuilder().fit(frame).transform(frame)
    assert out.loc[0, "pickup_hour"] == 8
    assert out.loc[0, "pickup_day_of_week"] == 3  # Wednesday
