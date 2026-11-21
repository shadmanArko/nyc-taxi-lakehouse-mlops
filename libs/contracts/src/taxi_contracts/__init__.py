"""Versioned contracts shared between pipeline stages and services.

Components never import each other's code. They agree only on the schemas in
this package (and on table schemas and model-registry aliases documented in
docs/architecture.md). Breaking changes bump the major version.
"""

from taxi_contracts.datasets import (
    GOLD_TARGET,
    GOLD_TRIP_FEATURES,
    GOLD_TRIP_FEATURES_TABLE,
    RAW_YELLOW_REQUIRED,
    TypeFamily,
)
from taxi_contracts.prediction import (
    CONTRACT_VERSION,
    MAX_LOCATION_ID,
    MIN_LOCATION_ID,
    PredictionResponse,
    TripFeatures,
)

__all__ = [
    "CONTRACT_VERSION",
    "GOLD_TARGET",
    "GOLD_TRIP_FEATURES",
    "GOLD_TRIP_FEATURES_TABLE",
    "MAX_LOCATION_ID",
    "MIN_LOCATION_ID",
    "RAW_YELLOW_REQUIRED",
    "PredictionResponse",
    "TripFeatures",
    "TypeFamily",
]
