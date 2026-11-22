"""Fit -> (tune) -> evaluate -> log to MLflow -> register as challenger."""

import logging
import os
import subprocess
from dataclasses import dataclass, field
from importlib.metadata import version as package_version
from typing import Any, Final

import mlflow
import numpy as np
import optuna
import pandas as pd
from mlflow.models import infer_signature
from sklearn.pipeline import Pipeline

from taxi_contracts import CONTRACT_VERSION, GOLD_TARGET
from taxi_features import canonical_inputs
from taxi_train import models
from taxi_train.data import Snapshot, Split, temporal_split
from taxi_train.evaluate import metrics, slice_rmse

log = logging.getLogger("taxi_train")

# MLflow saves sklearn models with skops (no pickle): loading refuses any type not on an
# explicit allow-list. These are exactly the types our own pipelines are built from.
SKOPS_TRUSTED_TYPES: Final = [
    "collections.OrderedDict",
    "lightgbm.basic.Booster",
    "lightgbm.sklearn.LGBMRegressor",
    "sklearn.model_selection._split.KFold",
    "sklearn.tree._tree.Tree",
    "taxi_features.pipeline.TripFeatureBuilder",
]

MODEL_NAME: Final = "taxi-trip-duration"
EXPERIMENT: Final = "taxi-trip-duration"
CHALLENGER: Final = "challenger"


@dataclass
class Result:
    model_name: str
    run_id: str
    params: dict[str, Any]
    validation: dict[str, float]
    test: dict[str, float]
    test_slices: dict[str, float]
    registered_version: str | None = None
    trials: list[dict[str, Any]] = field(default_factory=list)


def _xy(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    return canonical_inputs(frame), frame[GOLD_TARGET]


def _sample(frame: pd.DataFrame, n: int | None) -> pd.DataFrame:
    if n is None or n >= len(frame):
        return frame
    return frame.sample(n=n, random_state=models.SEED)


def tune_lightgbm(
    split: Split, trials: int, sample: int | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Optuna (TPE) on the validation week; the test week is never seen here."""
    x_train, y_train = _xy(_sample(split.train, sample))
    x_val, y_val = _xy(split.validation)

    def objective(trial: optuna.Trial) -> float:
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 200, 1200, step=100),
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 31, 511, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 20, 500, log=True),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
        }
        pipe = models.build("lightgbm", params).fit(x_train, y_train)
        return metrics(y_val, pipe.predict(x_val))["rmse"]

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="minimize", sampler=optuna.samplers.TPESampler(seed=models.SEED)
    )
    study.optimize(objective, n_trials=trials)
    history = [{"number": t.number, "rmse": t.value, **t.params} for t in study.trials]
    log.info("best validation RMSE %.4f with %s", study.best_value, study.best_params)
    return dict(study.best_params), history


def _git_sha() -> str:
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH, dev metadata only
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip()


def run(
    snapshot: Snapshot,
    model_name: str,
    *,
    tune_trials: int = 0,
    tune_sample: int | None = 500_000,
    train_sample: int | None = None,
    register: bool = False,
    experiment: str = EXPERIMENT,
) -> Result:
    split = temporal_split(snapshot.frame)
    params: dict[str, Any] = {}
    history: list[dict[str, Any]] = []
    if tune_trials and model_name == "lightgbm":
        params, history = tune_lightgbm(split, tune_trials, tune_sample)

    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=model_name) as active:
        mlflow.set_tags(
            {
                "model_family": model_name,
                "snapshot_sha256": snapshot.sha256,
                "snapshot_uri": snapshot.uri,
                "source_month": snapshot.source_month,
                "contract_version": CONTRACT_VERSION,
                "git_sha": _git_sha(),
                # Serving installs the same taxi-features package (no code_paths: re-importing a
                # bundled copy next to the installed one breaks sklearn's output wrappers).
                "taxi_features_version": package_version("taxi-features"),
                "split": "temporal",
                **{f"split_{k}": v for k, v in split.ranges().items()},
            }
        )
        mlflow.log_params(
            {
                **models.effective_params(model_name, params),
                "train_sample": train_sample or "all",
                "tune_trials": tune_trials,
            }
        )
        mlflow.log_metrics(
            {
                "rows_train": len(split.train),
                "rows_validation": len(split.validation),
                "rows_test": len(split.test),
            }
        )

        # 1) fit on train, score validation (comparable across all candidates)
        x_train, y_train = _xy(_sample(split.train, train_sample))
        x_val, y_val = _xy(split.validation)
        validation = metrics(
            y_val, models.build(model_name, params).fit(x_train, y_train).predict(x_val)
        )
        mlflow.log_metrics({f"val_{k}": v for k, v in validation.items()})

        # 2) final model: refit on train + validation, score the untouched test week once
        final_train = _sample(pd.concat([split.train, split.validation]), train_sample)
        pipe: Pipeline = models.build(model_name, params).fit(*_xy(final_train))
        x_test, y_test = _xy(split.test)
        pred = pipe.predict(x_test)
        test = metrics(y_test, pred)
        slices = slice_rmse(split.test, y_test, pred)
        mlflow.log_metrics({f"test_{k}": v for k, v in test.items()})
        mlflow.log_metrics({f"test_rmse__{k}": v for k, v in slices.items()})
        if history:
            mlflow.log_table(pd.DataFrame(history), artifact_file="optuna_trials.json")
        _log_model(pipe, x_test, register)

        version = None
        if register:
            version = _register(active.info.run_id, test["rmse"], snapshot)
        return Result(
            model_name,
            active.info.run_id,
            models.effective_params(model_name, params),
            validation,
            test,
            slices,
            version,
            history,
        )


def _log_model(pipe: Pipeline, x_example: pd.DataFrame, register: bool) -> None:  # noqa: ARG001
    example = x_example.head(5)
    mlflow.sklearn.log_model(
        pipe,
        name="model",
        signature=infer_signature(example, np.asarray(pipe.predict(example))),
        input_example=example,
        skops_trusted_types=SKOPS_TRUSTED_TYPES,
    )


def _register(run_id: str, test_rmse: float, snapshot: Snapshot) -> str:
    client = mlflow.MlflowClient()
    version = mlflow.register_model(f"runs:/{run_id}/model", MODEL_NAME)
    client.set_model_version_tag(MODEL_NAME, version.version, "test_rmse", f"{test_rmse:.4f}")
    client.set_model_version_tag(MODEL_NAME, version.version, "snapshot_sha256", snapshot.sha256)
    client.set_registered_model_alias(MODEL_NAME, CHALLENGER, version.version)
    log.info("registered %s v%s as @%s", MODEL_NAME, version.version, CHALLENGER)
    return str(version.version)
