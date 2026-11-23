"""Wire the drift measures to real snapshots, the live champion and MLflow."""

import logging
from typing import Final

import mlflow
import numpy as np
from sklearn.metrics import root_mean_squared_error

from taxi_contracts import GOLD_TARGET
from taxi_features import canonical_inputs
from taxi_train.data import Snapshot
from taxi_train.monitor import DriftReport, add_performance, data_drift
from taxi_train.training import MODEL_NAME

log = logging.getLogger("taxi_train")

EXPERIMENT: Final = "taxi-monitoring"


def run_monitoring(
    reference: Snapshot, current: Snapshot, *, with_model: bool = True
) -> DriftReport:
    report = data_drift(
        reference.frame, current.frame, reference.source_month, current.source_month
    )
    if with_model:
        client = mlflow.MlflowClient()
        champion = client.get_model_version_by_alias(MODEL_NAME, "champion")
        model = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}/{champion.version}")
        pred = np.asarray(model.predict(canonical_inputs(current.frame)), dtype="float64")
        rmse_now = float(root_mean_squared_error(current.frame[GOLD_TARGET], pred))
        rmse_then = float(champion.tags.get("test_rmse", "nan"))
        add_performance(report, MODEL_NAME, str(champion.version), rmse_then, rmse_now)
    else:
        report.retrain_recommended = bool(report.reasons)

    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"drift {reference.source_month} -> {current.source_month}"):
        mlflow.set_tags(
            {
                "reference_month": reference.source_month,
                "current_month": current.source_month,
                "reference_snapshot_sha256": reference.sha256,
                "current_snapshot_sha256": current.sha256,
                "retrain_recommended": str(report.retrain_recommended).lower(),
            }
        )
        metrics = {f"psi_{k}": v for k, v in report.feature_psi.items()}
        metrics["psi_target_duration"] = report.target_psi
        if report.rmse_current is not None:
            metrics["rmse_current"] = report.rmse_current
            metrics["rmse_change_pct"] = report.rmse_change_pct or 0.0
        mlflow.log_metrics(metrics)
        mlflow.log_dict(report.to_dict(), "drift_report.json")
    for reason in report.reasons or ["no significant drift"]:
        log.info("monitor: %s", reason)
    return report
