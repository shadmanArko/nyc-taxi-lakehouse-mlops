"""Feature engineering that is part of the model, not of the caller.

The serving API sends only what a rider knows at request time (the ``TripFeatures``
contract: zones, distance, pickup time). Everything derived from it (hour, weekday,
zone target encodings) happens inside the sklearn pipeline built here and is logged
with the model, so training and serving run literally the same code.
"""

from taxi_features.pipeline import (
    INPUT_DTYPES,
    RAW_INPUTS,
    TripFeatureBuilder,
    build_pipeline,
    canonical_inputs,
    to_model_input,
)

__all__ = [
    "INPUT_DTYPES",
    "RAW_INPUTS",
    "TripFeatureBuilder",
    "build_pipeline",
    "canonical_inputs",
    "to_model_input",
]
