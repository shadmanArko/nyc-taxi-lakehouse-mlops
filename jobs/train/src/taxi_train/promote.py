"""Evaluation gate: promote @challenger to @champion only if it is genuinely better.

Rules (all must hold):
1. The challenger beats a mean-prediction baseline on the test week.
2. If a champion exists, both are scored on the *same* test week (the challenger's
   snapshot), and the challenger's RMSE is at least ``min_improvement`` lower.
3. No slice (airport, rush hour, long trips, ...) gets more than
   ``max_slice_regression`` worse than the champion's.

On promotion the old champion keeps the alias @previous, so rollback is one alias move.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Final

import mlflow
import numpy as np
from mlflow.exceptions import MlflowException

from taxi_contracts import GOLD_TARGET
from taxi_features import canonical_inputs
from taxi_train.data import Snapshot, temporal_split
from taxi_train.evaluate import metrics, slice_rmse
from taxi_train.training import CHALLENGER, MODEL_NAME

log = logging.getLogger("taxi_train")

CHAMPION: Final = "champion"
PREVIOUS: Final = "previous"


@dataclass
class Decision:
    promoted: bool
    challenger_version: str
    champion_version: str | None
    challenger_rmse: float
    champion_rmse: float | None
    baseline_rmse: float
    reasons: list[str] = field(default_factory=list)


def _version(client: mlflow.MlflowClient, alias: str) -> Any | None:
    try:
        return client.get_model_version_by_alias(MODEL_NAME, alias)
    except MlflowException:
        return None


def _score(uri: str, x: Any, y: Any, frame: Any) -> tuple[float, dict[str, float]]:
    pred = np.asarray(mlflow.pyfunc.load_model(uri).predict(x), dtype="float64")
    return metrics(y, pred)["rmse"], slice_rmse(frame, y, pred)


def evaluate_gate(
    snapshot: Snapshot,
    *,
    min_improvement: float = 0.005,
    max_slice_regression: float = 0.05,
    apply: bool = True,
) -> Decision:
    client = mlflow.MlflowClient()
    challenger = _version(client, CHALLENGER)
    if challenger is None:
        msg = f"no @{CHALLENGER} version of {MODEL_NAME} to evaluate"
        raise RuntimeError(msg)
    if challenger.tags.get("snapshot_sha256") not in (None, snapshot.sha256):
        log.warning(
            "challenger was trained on snapshot %s, gating on %s",
            challenger.tags.get("snapshot_sha256"),
            snapshot.sha256,
        )

    split = temporal_split(snapshot.frame)
    x_test, y_test = canonical_inputs(split.test), split.test[GOLD_TARGET]
    history = np.concatenate([split.train[GOLD_TARGET], split.validation[GOLD_TARGET]])
    baseline_rmse = metrics(y_test, np.full(len(y_test), history.mean()))["rmse"]

    cand_rmse, cand_slices = _score(
        f"models:/{MODEL_NAME}@{CHALLENGER}", x_test, y_test, split.test
    )
    reasons: list[str] = []
    ok = cand_rmse < baseline_rmse
    reasons.append(
        f"challenger RMSE {cand_rmse:.4f} vs mean baseline {baseline_rmse:.4f}: "
        + ("pass" if ok else "FAIL")
    )

    champion = _version(client, CHAMPION)
    champ_rmse: float | None = None
    if champion is not None and champion.version != challenger.version:
        champ_rmse, champ_slices = _score(
            f"models:/{MODEL_NAME}@{CHAMPION}", x_test, y_test, split.test
        )
        target = champ_rmse * (1 - min_improvement)
        better = cand_rmse <= target
        ok &= better
        reasons.append(
            f"challenger {cand_rmse:.4f} vs champion v{champion.version} "
            f"{champ_rmse:.4f} (needs <= {target:.4f}): " + ("pass" if better else "FAIL")
        )
        for name, champ_value in champ_slices.items():
            limit = champ_value * (1 + max_slice_regression)
            if name in cand_slices and cand_slices[name] > limit:
                ok = False
                reasons.append(f"slice {name}: {cand_slices[name]:.4f} > {limit:.4f} limit: FAIL")
    else:
        reasons.append("no current champion: first model to pass the baseline becomes champion")

    decision = Decision(
        ok,
        str(challenger.version),
        str(champion.version) if champion else None,
        cand_rmse,
        champ_rmse,
        baseline_rmse,
        reasons,
    )
    for line in reasons:
        log.info("gate: %s", line)
    client.set_model_version_tag(
        MODEL_NAME, challenger.version, "gate_result", "promoted" if ok else "rejected"
    )
    if ok and apply:
        if champion is not None and champion.version != challenger.version:
            client.set_registered_model_alias(MODEL_NAME, PREVIOUS, champion.version)
        client.set_registered_model_alias(MODEL_NAME, CHAMPION, challenger.version)
        log.info("promoted v%s to @%s", challenger.version, CHAMPION)
    return decision
