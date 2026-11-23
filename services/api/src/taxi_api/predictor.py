"""The seam between the API and whatever model it serves.

The API depends on the ``Predictor`` protocol. ``MlflowPredictor`` serves a version from
the MLflow registry; tests use a fake. The model is a full sklearn pipeline built from
the same feature code as training (``taxi_features``), so serving cannot drift from it.
"""

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
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


class ModelCache:
    """Local copies of served versions, so a cold start needs no registry round trip.

    Layout: ``<root>/<name>/<version>/`` (an MLflow model directory) and
    ``<root>/<name>/CURRENT`` (the version last served). Keeps the newest two versions.
    """

    KEEP: int = 2

    def __init__(self, root: Path) -> None:
        self.root = root

    def _dir(self, name: str) -> Path:
        return self.root / name

    def current_version(self, name: str) -> str | None:
        marker = self._dir(name) / "CURRENT"
        if not marker.exists():
            return None
        version = marker.read_text().strip()
        return version if (self._dir(name) / version / "MLmodel").exists() else None

    def fetch(self, ref: ModelRef, version: str) -> Path:
        """Download ``version`` once (atomically: temp dir, then rename) and return its path."""
        import mlflow

        target = self._dir(ref.name) / version
        if (target / "MLmodel").exists():
            return target
        self._dir(ref.name).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._dir(ref.name)) as tmp:
            local = mlflow.artifacts.download_artifacts(
                f"models:/{ref.name}/{version}", dst_path=tmp
            )
            Path(local).replace(target)
        return target

    def mark_current(self, name: str, version: str) -> None:
        marker = self._dir(name) / "CURRENT"
        partial = marker.with_suffix(".tmp")
        partial.write_text(f"{version}\n")
        partial.replace(marker)
        versions = sorted(
            (p for p in self._dir(name).iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: int(p.name),
        )
        for stale in versions[: -self.KEEP]:
            if stale.name != version:
                shutil.rmtree(stale, ignore_errors=True)


def load_version(ref: ModelRef, version: str, cache: ModelCache | None = None) -> MlflowPredictor:
    """Download and load an exact version (pinned, so an alias move mid-load is harmless)."""
    import mlflow

    log.info("loading %s v%s", ref.name, version)
    if cache is None:
        model = mlflow.pyfunc.load_model(f"models:/{ref.name}/{version}")
    else:
        model = mlflow.pyfunc.load_model(str(cache.fetch(ref, version)))
        cache.mark_current(ref.name, version)
    return MlflowPredictor(ref.name, version, model)


def load_cached(ref: ModelRef, cache: ModelCache) -> MlflowPredictor | None:
    """The last served version from local disk, without contacting the registry."""
    import mlflow

    version = cache.current_version(ref.name)
    if version is None:
        return None
    log.info("warm start: %s v%s from local cache", ref.name, version)
    model = mlflow.pyfunc.load_model(str(cache.root / ref.name / version))
    return MlflowPredictor(ref.name, version, model)
