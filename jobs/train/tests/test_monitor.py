from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
import pytest

from taxi_train.data import load_local
from taxi_train.monitor import (
    PSI_MODERATE,
    PSI_SIGNIFICANT,
    add_performance,
    data_drift,
    level,
    psi_categorical,
    psi_numeric,
)
from taxi_train.monitor_run import run_monitoring
from taxi_train.promote import evaluate_gate
from taxi_train.training import run
from test_train import gold_frame, parquet_bytes


def test_psi_is_zero_for_identical_data() -> None:
    values = pd.Series(np.random.default_rng(0).gamma(2, 1.5, 5000))
    assert psi_numeric(values, values) == pytest.approx(0.0, abs=1e-9)
    hours = pd.Series(np.random.default_rng(1).integers(0, 24, 5000))
    assert psi_categorical(hours, hours) == pytest.approx(0.0, abs=1e-9)


def test_psi_detects_a_shift() -> None:
    rng = np.random.default_rng(0)
    reference = pd.Series(rng.gamma(2, 1.5, 20000))
    shifted = pd.Series(rng.gamma(2, 1.5, 20000) * 1.8)  # trips got much longer
    assert psi_numeric(reference, shifted) > PSI_SIGNIFICANT


def test_levels() -> None:
    assert level(0.01) == "stable"
    assert level(PSI_MODERATE) == "moderate"
    assert level(PSI_SIGNIFICANT + 0.01) == "significant"


def test_rare_zones_are_pooled() -> None:
    rng = np.random.default_rng(0)
    reference = pd.Series(rng.integers(1, 200, 10000))
    current = pd.Series(rng.integers(1, 200, 10000))
    assert psi_categorical(reference, current, top=30) < PSI_MODERATE


def test_report_flags_drift_and_degradation() -> None:
    reference = gold_frame(days=20, per_day=200, seed=0)
    current = gold_frame(days=20, per_day=200, seed=1)
    current["trip_distance"] = current["trip_distance"] * 2.5
    report = data_drift(reference, current, "2025-01", "2025-02")
    assert report.feature_level["trip_distance"] == "significant"
    assert report.feature_level["pickup_hour"] == "stable"
    add_performance(report, "m", "2", rmse_reference=4.0, rmse_current=4.8)
    assert report.rmse_change_pct == pytest.approx(20.0)
    assert report.retrain_recommended is True
    assert any("RMSE" in r for r in report.reasons)


def test_stable_month_does_not_recommend_retraining() -> None:
    report = data_drift(gold_frame(seed=0), gold_frame(seed=1), "2025-01", "2025-02")
    add_performance(report, "m", "2", rmse_reference=4.0, rmse_current=4.1)
    assert report.retrain_recommended is False
    assert report.reasons == []


def test_monitoring_end_to_end_with_champion(tmp_path: Path) -> None:
    db = f"sqlite:///{tmp_path / 'mlflow.db'}"
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(db)
    mlflow.set_registry_uri(db)
    try:
        jan, feb = tmp_path / "jan.parquet", tmp_path / "feb.parquet"
        jan.write_bytes(parquet_bytes(gold_frame(seed=0)))
        feb.write_bytes(parquet_bytes(gold_frame(seed=1)))
        reference, current = load_local(jan, "2025-01"), load_local(feb, "2025-02")
        run(reference, "lightgbm", register=True)
        evaluate_gate(reference)
        report = run_monitoring(reference, current)
        assert report.model_version == "1"
        assert report.rmse_current is not None
        assert report.rmse_reference is not None
        runs = mlflow.search_runs(experiment_names=["taxi-monitoring"])
        assert len(runs) == 1
        assert runs.iloc[0]["tags.current_month"] == "2025-02"
    finally:
        mlflow.set_tracking_uri(previous)
