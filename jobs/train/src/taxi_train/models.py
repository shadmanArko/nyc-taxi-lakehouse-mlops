"""Candidate models. Every one is wrapped in the same feature pipeline."""

from collections.abc import Callable, Mapping
from typing import Any, Final

from lightgbm import LGBMRegressor
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline

from taxi_features import build_pipeline

SEED: Final = 42

# Bounded so the forest stays trainable on millions of rows and small enough to serve:
# each tree sees a 25% bootstrap sample, and leaves need 50 trips.
RF_DEFAULTS: Final[dict[str, Any]] = {
    "n_estimators": 60,
    "max_depth": 18,
    "min_samples_leaf": 50,
    "max_samples": 0.25,
    "max_features": 0.6,
}

LGBM_DEFAULTS: Final[dict[str, Any]] = {
    "n_estimators": 600,
    "learning_rate": 0.05,
    "num_leaves": 127,
    "min_child_samples": 100,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
}


def _mean(_: Mapping[str, Any]) -> Pipeline:
    return build_pipeline(DummyRegressor(strategy="mean"), random_state=SEED)


def _random_forest(params: Mapping[str, Any]) -> Pipeline:
    return build_pipeline(
        RandomForestRegressor(**{**RF_DEFAULTS, **params}, n_jobs=-1, random_state=SEED),
        random_state=SEED,
    )


def _lightgbm(params: Mapping[str, Any]) -> Pipeline:
    return build_pipeline(
        LGBMRegressor(**{**LGBM_DEFAULTS, **params}, n_jobs=-1, random_state=SEED, verbose=-1),
        random_state=SEED,
    )


CANDIDATES: Final[dict[str, Callable[[Mapping[str, Any]], Pipeline]]] = {
    "mean_baseline": _mean,
    "random_forest": _random_forest,
    "lightgbm": _lightgbm,
}


def build(name: str, params: Mapping[str, Any] | None = None) -> Pipeline:
    if name not in CANDIDATES:
        msg = f"unknown model {name!r}; choose from {sorted(CANDIDATES)}"
        raise ValueError(msg)
    return CANDIDATES[name](params or {})


def effective_params(name: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    defaults = {"random_forest": RF_DEFAULTS, "lightgbm": LGBM_DEFAULTS}.get(name, {})
    return {**defaults, **(params or {})}
