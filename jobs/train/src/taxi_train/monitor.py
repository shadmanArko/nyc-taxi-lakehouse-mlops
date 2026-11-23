"""Drift monitoring: does a newer month still look like the data the champion learned from?

Three questions, each with an industry-standard measure:
1. Data drift: Population Stability Index (PSI) per input feature, reference = the
   training month, current = the newer month. PSI < 0.1 stable, 0.1-0.25 moderate,
   > 0.25 significant (the usual credit-risk/MLOps thresholds).
2. Target drift: PSI on the trip duration itself (did trips get longer?).
3. Performance: the live champion's RMSE on the newer month vs its test-week RMSE.
   More than 10% worse = retraining recommended.
The report is logged to MLflow (experiment ``taxi-monitoring``) for the portal.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Final

import numpy as np
import pandas as pd

from taxi_contracts import GOLD_TARGET

PSI_MODERATE: Final = 0.10
PSI_SIGNIFICANT: Final = 0.25
RMSE_DEGRADATION_ALERT: Final = 0.10  # 10% worse than at training time
EPS: Final = 1e-6

NUMERIC_FEATURES: Final = ("trip_distance",)
CATEGORICAL_FEATURES: Final = (
    "pickup_hour",
    "pickup_day_of_week",
    "pickup_location_id",
    "dropoff_location_id",
)
TOP_ZONES: Final = 30  # zones beyond the 30 busiest are pooled into "other"


def level(psi: float) -> str:
    if psi >= PSI_SIGNIFICANT:
        return "significant"
    if psi >= PSI_MODERATE:
        return "moderate"
    return "stable"


def _psi(expected: np.ndarray, actual: np.ndarray) -> float:
    e = np.clip(expected, EPS, None)
    a = np.clip(actual, EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_numeric(reference: pd.Series, current: pd.Series, bins: int = 10) -> float:
    """Decile bins from the reference, so each reference bin holds ~10% of rows."""
    edges = np.unique(np.quantile(reference.to_numpy(dtype=float), np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    ref = np.histogram(reference, edges)[0] / len(reference)
    cur = np.histogram(current, edges)[0] / len(current)
    return _psi(ref, cur)


def psi_categorical(reference: pd.Series, current: pd.Series, top: int | None = None) -> float:
    categories = reference.value_counts()
    if top is not None and len(categories) > top:
        keep = set(categories.index[:top])
        reference = reference.where(reference.isin(keep), other=-1)
        current = current.where(current.isin(keep), other=-1)
    ref = reference.value_counts(normalize=True)
    cur = current.value_counts(normalize=True)
    index = ref.index.union(cur.index)
    return _psi(
        ref.reindex(index, fill_value=0).to_numpy(), cur.reindex(index, fill_value=0).to_numpy()
    )


@dataclass
class DriftReport:
    reference_month: str
    current_month: str
    rows_reference: int
    rows_current: int
    feature_psi: dict[str, float]
    feature_level: dict[str, str]
    target_psi: float
    target_level: str
    duration_mean_reference: float
    duration_mean_current: float
    model_name: str | None = None
    model_version: str | None = None
    rmse_reference: float | None = None
    rmse_current: float | None = None
    rmse_change_pct: float | None = None
    retrain_recommended: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def data_drift(
    reference: pd.DataFrame, current: pd.DataFrame, reference_month: str, current_month: str
) -> DriftReport:
    psi: dict[str, float] = {}
    for column in NUMERIC_FEATURES:
        psi[column] = psi_numeric(reference[column], current[column])
    for column in CATEGORICAL_FEATURES:
        top = TOP_ZONES if column.endswith("location_id") else None
        psi[column] = psi_categorical(reference[column], current[column], top)
    target = psi_numeric(reference[GOLD_TARGET], current[GOLD_TARGET])
    report = DriftReport(
        reference_month=reference_month,
        current_month=current_month,
        rows_reference=len(reference),
        rows_current=len(current),
        feature_psi={k: round(v, 4) for k, v in psi.items()},
        feature_level={k: level(v) for k, v in psi.items()},
        target_psi=round(target, 4),
        target_level=level(target),
        duration_mean_reference=round(float(reference[GOLD_TARGET].mean()), 3),
        duration_mean_current=round(float(current[GOLD_TARGET].mean()), 3),
    )
    for name, value in psi.items():
        if value >= PSI_SIGNIFICANT:
            report.reasons.append(f"significant drift in {name} (PSI {value:.3f})")
    if target >= PSI_SIGNIFICANT:
        report.reasons.append(f"significant target drift (PSI {target:.3f})")
    return report


def add_performance(
    report: DriftReport,
    model_name: str,
    model_version: str,
    rmse_reference: float,
    rmse_current: float,
) -> DriftReport:
    change = (rmse_current - rmse_reference) / rmse_reference
    report.model_name = model_name
    report.model_version = model_version
    report.rmse_reference = round(rmse_reference, 4)
    report.rmse_current = round(rmse_current, 4)
    report.rmse_change_pct = round(100 * change, 2)
    if change > RMSE_DEGRADATION_ALERT:
        report.reasons.append(
            f"champion RMSE {rmse_current:.3f} is {100 * change:.1f}% worse than at training "
            f"({rmse_reference:.3f})"
        )
    report.retrain_recommended = bool(report.reasons)
    return report
