"""The seam between the API and whatever model it serves.

The API depends on this protocol, not on MLflow or scikit-learn. Phase 5 adds an
implementation that loads the registry's ``champion`` model; tests use a fake.
"""

from typing import Protocol

from taxi_contracts import TripFeatures


class Predictor(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    def predict(self, features: TripFeatures) -> float:
        """Return the predicted trip duration in minutes."""
        ...
