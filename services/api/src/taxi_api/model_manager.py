"""Keeps the served model in step with a registry alias (hot reload, no restart).

Every refresh asks the registry which version ``@champion`` points at (one cheap call).
Only when that version changes is the new model downloaded and loaded, and only after
it loaded successfully is it swapped in. A failed refresh keeps serving the old model.
"""

import logging
from collections.abc import Callable

from taxi_api.predictor import ModelRef, Predictor, load_version, resolve_version

log = logging.getLogger("taxi_api")

Resolver = Callable[[ModelRef], str]
Loader = Callable[[ModelRef, str], Predictor]


class ModelManager:
    def __init__(
        self, ref: ModelRef, resolve: Resolver = resolve_version, load: Loader = load_version
    ) -> None:
        self.ref = ref
        self._resolve = resolve
        self._load = load
        self.current: Predictor | None = None

    def refresh(self) -> bool:
        """Load the alias's version if it changed. Returns True when a new model went live."""
        version = self._resolve(self.ref)
        if self.current is not None and self.current.model_version == version:
            return False
        loaded = self._load(self.ref, version)  # may raise: then the old model stays live
        previous = self.current.model_version if self.current else None
        self.current = loaded
        log.info("serving %s v%s (was %s)", self.ref.name, version, previous or "nothing")
        return True
