# Assignment brief and requirement mapping

Origin: a model-deployment course project. Train a regression model that predicts the
duration of NYC yellow taxi trips (January 2025 data), track experiments locally with MLflow,
and serve predictions with FastAPI.

## Required

| # | Requirement | Where it is met | Status |
|---|---|---|---|
| 1 | Load and clean January 2025 yellow taxi data; target `duration` | EDA notebook, ingest job, Lakeflow bronze/silver, dbt gold (`duration_min`) | Done (Phase 2) |
| 2 | Train/validation split | Temporal train / validation / test split (days 1-17 / 18-24 / 25-31) in `jobs/train` | Done (Phase 4) |
| 3 | Train a scikit-learn `RandomForestRegressor` | `taxi-train --model random_forest`: test RMSE 4.52 min | Done (Phase 4) |
| 4 | Evaluate with RMSE, log runs to MLflow | RMSE/MAE/R² overall and per slice, logged to self-hosted MLflow with data lineage | Done (Phase 4) |
| 5 | Reusable preprocessing and model loading for training and API | One sklearn pipeline (preprocessing + model) logged to MLflow; the API loads it from the registry (`champion` alias), and request/response shapes come from `libs/contracts` (Phases 4-5) | Planned |
| 6 | FastAPI app with a `/predict` endpoint | `services/api` | Skeleton done (Phase 1), model in Phase 5 |
| 7 | Run the API locally (uvicorn or Docker) | `make up` (Docker, hardened) | Done (Phase 1) |
| 8 | Send a sample request | `sample-request.json` + `curl` (Phase 5) | Planned |
| 9 | Document RMSE and follow-up ideas in the README | README "Assignment" table (Phases 4, 7) | Planned |

## Stretch

| Stretch goal | Where | Status |
|---|---|---|
| Tests for pipeline, model, API | pytest (API + contracts now), dbt tests, model tests | Started (Phase 1) |
| Hyperparameter tuning with GridSearchCV or Optuna | Optuna (TPE, 30 trials) tuning LightGBM on the validation week | Done (Phase 4) |
| Compare tuned vs baseline and judge whether it was worth it | Tuned LightGBM 4.29 vs RandomForest 4.52 min: 5% better and better on every slice; worth it (training ~30 min on a laptop) | Done (Phase 4) |

## Questions to answer

1. What is the RMSE of your final model?
2. What would you do differently if you had more time?
