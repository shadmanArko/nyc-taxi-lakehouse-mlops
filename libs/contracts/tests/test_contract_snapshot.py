"""Guards against silent contract changes.

If a schema changes, this test fails until the change is made deliberately:
bump CONTRACT_VERSION (and the package version), then regenerate with
    UPDATE_CONTRACT_SNAPSHOT=1 uv run pytest libs/contracts
"""

import json
import os
from importlib.metadata import version
from pathlib import Path

from taxi_contracts import CONTRACT_VERSION, PredictionResponse, TripFeatures

SNAPSHOT = Path(__file__).parent.parent / "schemas" / "prediction.schema.json"


def _current_schema() -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "TripFeatures": TripFeatures.model_json_schema(),
        "PredictionResponse": PredictionResponse.model_json_schema(),
    }


def test_schema_matches_committed_snapshot() -> None:
    current = _current_schema()
    if os.environ.get("UPDATE_CONTRACT_SNAPSHOT") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    committed = json.loads(SNAPSHOT.read_text())
    assert current == committed, (
        "Contract schema changed. Bump CONTRACT_VERSION and regenerate the snapshot "
        "(UPDATE_CONTRACT_SNAPSHOT=1 uv run pytest libs/contracts)."
    )


def test_package_version_equals_contract_version() -> None:
    """One version number, two places: keep them from drifting."""
    assert version("taxi-contracts") == CONTRACT_VERSION
