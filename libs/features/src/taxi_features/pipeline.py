"""Raw request fields -> model features -> estimator, as one sklearn Pipeline."""

from collections.abc import Iterable, Mapping
from typing import Any, Final, Self

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import TargetEncoder

# Exactly the TripFeatures contract fields, in this order.
RAW_INPUTS: Final = (
    "pickup_location_id",
    "dropoff_location_id",
    "trip_distance",
    "pickup_datetime",
)

ZONE_COLUMNS: Final = ("pickup_zone", "dropoff_zone", "zone_pair")
NUMERIC_COLUMNS: Final = (
    "trip_distance",
    "log_trip_distance",
    "pickup_hour",
    "pickup_day_of_week",
    "is_weekend",
    "hour_sin",
    "hour_cos",
)


class TripFeatureBuilder(TransformerMixin, BaseEstimator):  # type: ignore[misc]
    """Stateless: derive features from the raw request fields.

    * hour, ISO weekday (1 = Monday, same as the gold table), weekend flag
    * cyclic hour (23:00 and 00:00 are neighbours)
    * zone pair as one categorical (route effects: e.g. Midtown -> JFK)
    """

    def fit(self, x: pd.DataFrame, y: Any = None) -> Self:  # noqa: ARG002 - sklearn API
        missing = set(RAW_INPUTS) - set(x.columns)
        if missing:
            msg = f"missing input columns: {sorted(missing)}"
            raise ValueError(msg)
        return self

    def transform(self, x: pd.DataFrame) -> pd.DataFrame:
        pickup = pd.to_datetime(x["pickup_datetime"])
        if getattr(pickup.dt, "tz", None) is not None:
            msg = "pickup_datetime must be naive NYC local time (TLC convention)"
            raise ValueError(msg)
        hour = pickup.dt.hour.astype("int64")
        dow = (pickup.dt.day_of_week + 1).astype("int64")  # pandas Monday=0 -> ISO Monday=1
        distance = x["trip_distance"].astype("float64")
        pu = x["pickup_location_id"].astype("int64")
        do = x["dropoff_location_id"].astype("int64")
        return pd.DataFrame(
            {
                "pickup_zone": pu,
                "dropoff_zone": do,
                "zone_pair": pu * 1000 + do,
                "trip_distance": distance,
                "log_trip_distance": np.log1p(distance),
                "pickup_hour": hour,
                "pickup_day_of_week": dow,
                "is_weekend": (dow >= 6).astype("int64"),
                "hour_sin": np.sin(2 * np.pi * hour / 24),
                "hour_cos": np.cos(2 * np.pi * hour / 24),
            },
            index=x.index,
        )

    def get_feature_names_out(self, input_features: Iterable[str] | None = None) -> np.ndarray:  # noqa: ARG002
        return np.asarray([*ZONE_COLUMNS, *NUMERIC_COLUMNS], dtype=object)


def build_pipeline(model: RegressorMixin, random_state: int = 42) -> Pipeline:
    """Features -> zone target encoding (cross-fitted, no target leakage) -> model."""
    encode = ColumnTransformer(
        [
            (
                "zones",
                TargetEncoder(
                    target_type="continuous",
                    # cross-fitting: each row is encoded by folds that exclude it
                    cv=KFold(n_splits=5, shuffle=True, random_state=random_state),
                ),
                list(ZONE_COLUMNS),
            ),
            ("numeric", "passthrough", list(NUMERIC_COLUMNS)),
        ],
        verbose_feature_names_out=False,
    )
    return Pipeline([("features", TripFeatureBuilder()), ("encode", encode), ("model", model)])


# One canonical input format for training, the logged signature and serving. Gold stores
# zone IDs as int32 while an API request yields int64; MLflow's schema enforcement rightly
# refuses int64 -> int32, so everything is normalised to these dtypes first.
INPUT_DTYPES: Final = {
    "pickup_location_id": "int64",
    "dropoff_location_id": "int64",
    "trip_distance": "float64",
    "pickup_datetime": "datetime64[ns]",
}


def _naive_wall_clock(values: pd.Series) -> pd.Series:
    """Return naive NYC wall-clock times.

    Databricks returns the gold column as TIMESTAMP labelled UTC, but the digits are the
    TLC's NYC wall-clock time (verified on the Jan 2025 snapshot: the labelled hour equals
    gold's ``pickup_hour`` on 100% of rows). So a UTC label is dropped *without* shifting
    the clock. Any other timezone means a real conversion happened upstream: refuse it.
    """
    times = pd.to_datetime(values)
    tz = getattr(times.dt, "tz", None)
    if tz is None:
        return times
    if str(tz) not in {"UTC", "Etc/UTC"}:
        msg = f"pickup_datetime has timezone {tz}; expected naive NYC wall-clock time"
        raise ValueError(msg)
    return times.dt.tz_localize(None)


def canonical_inputs(frame: pd.DataFrame) -> pd.DataFrame:
    """Select the raw inputs, in contract order, with the canonical dtypes."""
    selected = frame[list(RAW_INPUTS)].copy()
    selected["pickup_datetime"] = _naive_wall_clock(selected["pickup_datetime"])
    return selected.astype(INPUT_DTYPES)


def to_model_input(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """API requests (dicts in the TripFeatures shape) -> the DataFrame the model expects."""
    return canonical_inputs(pd.DataFrame(list(rows), columns=list(RAW_INPUTS)))
