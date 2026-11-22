"""The seam between the API and whatever model it serves.

The API depends on the ``Predictor`` protocol. ``MlflowPredictor`` serves a version from
the MLflow registry; tests use a fake. The model is a full sklearn pipeline built from
the same feature code as training (``taxi_features``), so serving cannot drift from it.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from taxi_contracts import TripFeatures

log = logging.getLogger("taxi_api")

_ALIAS_URI = re.compile(r"^models:/(?P<name>[^/@]+)@(?P<alias>[\w-]+)$")


class Predictor(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    def predict(self, features: TripFeatures) -> float:
        """Return the predicted trip duration in minutes."""
        ...


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A registered model alias, e.g. ``models:/taxi-trip-duration@champion``."""

    name: str
    alias: str

    @classmethod
    def parse(cls, uri: str) -> "ModelRef":
        match = _ALIAS_URI.match(uri)
        if not match:
            msg = f"model URI must look like models:/<name>@<alias>, got {uri!r}"
            raise ValueError(msg)
        return cls(match["name"], match["alias"])


class MlflowPredictor:
    """One exact registry version, loaded as an MLflow pyfunc model."""

    def __init__(self, name: str, version: str, model: Any) -> None:
        self._name = name
        self._version = version
        self._model = model

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def model_version(self) -> str:
        return self._version

    def predict(self, features: TripFeatures) -> float:
        from taxi_features import to_model_input

        frame = to_model_input([features.model_dump()])
        return float(self._model.predict(frame)[0])


def resolve_version(ref: ModelRef) -> str:
    """Which version the alias points at right now (cheap: one registry call)."""
    import mlflow

    return str(mlflow.MlflowClient().get_model_version_by_alias(ref.name, ref.alias).version)


def load_version(ref: ModelRef, version: str) -> MlflowPredictor:
    """Download and load an exact version (pinned, so an alias move mid-load is harmless)."""
    import mlflow

    log.info("loading %s v%s", ref.name, version)
    model = mlflow.pyfunc.load_model(f"models:/{ref.name}/{version}")
    return MlflowPredictor(ref.name, version, model)
