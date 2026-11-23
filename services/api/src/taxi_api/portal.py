"""The portal snapshot (every department's live numbers) served read-only to the web UI.

Published by ``taxi-portal-snapshot`` as an MLflow artifact (experiment ``taxi-portal``).
The API refreshes it alongside the model and keeps a copy on disk, so a cold start serves
the last snapshot immediately, even while MLflow sleeps.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

log = logging.getLogger("taxi_api")

EXPERIMENT: Final = "taxi-portal"

Fetcher = Callable[[], tuple[str, dict[str, Any]] | None]


def fetch_latest_from_mlflow() -> tuple[str, dict[str, Any]] | None:
    """(run_id, snapshot) of the newest published portal snapshot, or None."""
    import mlflow

    runs = mlflow.search_runs(
        experiment_names=[EXPERIMENT],
        filter_string="tags.portal_snapshot = 'true'",
        order_by=["attributes.start_time DESC"],
        max_results=1,
        output_format="list",
    )
    if not runs:
        return None
    run_id = runs[0].info.run_id
    return run_id, mlflow.artifacts.load_dict(f"runs:/{run_id}/portal.json")


class PortalStore:
    def __init__(
        self, cache_dir: Path | None = None, fetch: Fetcher = fetch_latest_from_mlflow
    ) -> None:
        self._file = cache_dir / "portal.json" if cache_dir else None
        self._fetch = fetch
        self.run_id: str | None = None
        self.snapshot: dict[str, Any] | None = None

    def warm_start(self) -> bool:
        if self._file is None or not self._file.exists():
            return False
        cached = json.loads(self._file.read_text())
        self.run_id, self.snapshot = cached.get("run_id"), cached.get("snapshot")
        return self.snapshot is not None

    def refresh(self) -> bool:
        latest = self._fetch()
        if latest is None or latest[0] == self.run_id:
            return False
        self.run_id, self.snapshot = latest
        if self._file is not None:
            partial = self._file.with_suffix(".tmp")
            partial.write_text(json.dumps({"run_id": self.run_id, "snapshot": self.snapshot}))
            partial.replace(self._file)
        log.info("portal snapshot %s is live", self.run_id)
        return True
