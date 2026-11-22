"""Metrics overall and per slice. A model that wins on average but fails airport trips
or rush hour is not an improvement, so the promotion gate looks at slices too."""

from collections.abc import Callable
from typing import Final

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error

AIRPORT_ZONES: Final = frozenset({1, 132, 138})  # EWR, JFK, LaGuardia

SLICES: Final[dict[str, Callable[[pd.DataFrame], pd.Series]]] = {
    "distance_lt_2mi": lambda f: f["trip_distance"] < 2,
    "distance_2_to_5mi": lambda f: (f["trip_distance"] >= 2) & (f["trip_distance"] < 5),
    "distance_5_to_10mi": lambda f: (f["trip_distance"] >= 5) & (f["trip_distance"] < 10),
    "distance_ge_10mi": lambda f: f["trip_distance"] >= 10,
    "weekday_am_peak": lambda f: (f["pickup_day_of_week"] <= 5) & f["pickup_hour"].between(7, 9),
    "weekday_pm_peak": lambda f: (f["pickup_day_of_week"] <= 5) & f["pickup_hour"].between(16, 19),
    "night_0_to_5": lambda f: f["pickup_hour"] <= 5,
    "weekend": lambda f: f["pickup_day_of_week"] >= 6,
    "airport": lambda f: (
        f["pickup_location_id"].isin(AIRPORT_ZONES) | f["dropoff_location_id"].isin(AIRPORT_ZONES)
    ),
}


def metrics(y_true: np.ndarray | pd.Series, y_pred: np.ndarray) -> dict[str, float]:
    error = np.abs(np.asarray(y_true) - y_pred)
    return {
        "rmse": float(root_mean_squared_error(y_true, y_pred)),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
        "p90_abs_error": float(np.quantile(error, 0.9)),
    }


def slice_rmse(frame: pd.DataFrame, y_true: pd.Series, y_pred: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, select in SLICES.items():
        mask = select(frame).to_numpy()
        if mask.sum() >= 100:  # too few trips -> too noisy to judge
            out[name] = float(root_mean_squared_error(y_true[mask], y_pred[mask]))
    return out
