# ADR-0008: Model training, evaluation and gated promotion

- **Status:** Accepted
- **Date:** 2026-11-22

## Context

The brief asks for a scikit-learn `RandomForestRegressor`, RMSE, MLflow tracking and an
API that serves predictions. A production team also needs: no train/serve skew, honest
evaluation, reproducible lineage from a prediction back to its data, and a safe way to
replace a model.

## Decision

**One pipeline from raw request fields.** Every candidate is the same sklearn pipeline:
`TripFeatureBuilder` (hour, ISO weekday, weekend, cyclic hour, zone pair) → cross-fitted
`TargetEncoder` for pickup zone, dropoff zone and zone pair → the estimator. Its input is
exactly the API contract (`TripFeatures`), with one canonical dtype set
(`taxi_features.canonical_inputs`). The serving API therefore runs the training code
verbatim. `taxi-features` is a versioned package that serving installs; its version is
recorded on every run. (Bundling it via MLflow `code_paths` was tried and rejected:
re-importing a second copy beside the installed one broke sklearn's output wrappers.)

**Temporal split.** For one month: train = days 1-17, validation = 18-24, test = 25-31.
A random split would leak days across sets and overstate accuracy. Tuning (Optuna TPE)
only sees validation; the final model is refit on train + validation and scored once on
the untouched test week.

**Candidates.** Mean baseline, `RandomForestRegressor` (required; bounded with 25%
bootstrap samples, depth 18, leaves ≥ 50 so it trains on millions of rows and stays
servable), and LightGBM tuned with Optuna.

**Lineage.** Each MLflow run records the gold snapshot SHA-256 (training data bytes, see
ADR-0007), source month, contract version, git commit, feature-package version, split
ranges and the Optuna trial table. Models are saved with skops (no pickle) and an explicit
allow-list of trusted types, plus an input signature that MLflow enforces at load time.

**Gated promotion via registry aliases.** Training registers `@challenger`. The gate
re-scores `@champion` on the challenger's *own* test week (same rows) and promotes only if
the challenger (1) beats the mean baseline, (2) improves RMSE by ≥ 0.5%, and (3) regresses
no slice (distance bands, AM/PM peaks, night, weekend, airport) by more than 5%. The old
champion keeps `@previous` for one-step rollback. In Airflow, "not promoted" is a
*skipped* task, not a failure.

## Results (January 2025, test week Jan 25-31, 788,828 trips)

| Model | Val RMSE | Test RMSE | Test MAE | Test R² |
|---|---|---|---|---|
| Mean baseline | 10.64 | 11.06 | 7.63 | 0.00 |
| RandomForest | 4.53 | 4.52 | 2.73 | 0.833 |
| LightGBM + Optuna (30 trials) | 4.32 | **4.29** | 2.58 | 0.849 |

LightGBM is 5.0% better overall and better on every slice (e.g. airport 7.00 vs 7.15,
AM peak 5.25 vs 5.51, ≥ 10 mi 9.81 vs 10.33). The gate promoted the forest first (v1),
then LightGBM (v2), keeping v1 as `@previous`.

## Consequences and lessons from the first real run

- **A swallowed exception hid a serving bug.** Under the test suite's warnings-as-errors
  policy, an MLflow schema hint became an exception that MLflow caught internally, so the
  model was logged without an enforced signature. Once fixed, enforcement exposed a real
  mismatch: gold stores zone IDs as int32, API requests produce int64. Fixed with one
  canonical input dtype set used by training, the signature and serving.
- **Gold timestamps come back labelled UTC.** Databricks returns the dbt `timestamp`
  column as UTC, but the digits are NYC wall-clock time (labelled hour = `pickup_hour` on
  100% of rows). The feature code drops a UTC label without shifting and rejects any
  other timezone. Better long-term fix: keep gold as `TIMESTAMP_NTZ` on Databricks.
- **MLflow 3's background job system** (GenAI evaluation) starts ~6 extra processes
  (~1.5 GiB) and OOM-killed the 1 GiB container 31 times; it is disabled
  (`MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false`), leaving the server at ~390 MiB.
- Retraining on new months is automatic: the refresh DAG emits an asset event that
  schedules `taxi_train_and_promote`.
