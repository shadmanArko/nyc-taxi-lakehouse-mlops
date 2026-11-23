"""Keeps the served model in step with a registry alias (hot reload, no restart).

Every refresh asks the registry which version ``@champion`` points at (one cheap call).
Only when that version changes is the new model downloaded and loaded, and only after
it loaded successfully is it swapped in. A failed refresh keeps serving the old model.
"""

import functools
import logging
from collections.abc import Callable

from taxi_api.predictor import (
    ModelCache,
    ModelRef,
    Predictor,
    load_cached,
    load_version,
    resolve_version,
)

log = logging.getLogger("taxi_api")

Resolver = Callable[[ModelRef], str]
Loader = Callable[[ModelRef, str], Predictor]


class ModelManager:
    def __init__(
        self,
        ref: ModelRef,
        resolve: Resolver = resolve_version,
        load: Loader | None = None,
        cache: ModelCache | None = None,
    ) -> None:
        self.ref = ref
        self.cache = cache
        self._resolve = resolve
        self._load = load or functools.partial(load_version, cache=cache)
        self.current: Predictor | None = None

    def warm_start(self) -> bool:
        """Serve the locally cached version immediately (scale-to-zero cold starts).

        No registry call: the API can answer within seconds of waking, and the normal
        refresh then checks the registry for a newer champion in the background.
        """
        if self.current is not None or self.cache is None:
            return False
        cached = load_cached(self.ref, self.cache)
        if cached is None:
            return False
        self.current = cached
        return True

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
