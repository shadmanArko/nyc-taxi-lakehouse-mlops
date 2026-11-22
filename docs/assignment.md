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
| 5 | Reusable preprocessing and model loading for training and API | `libs/features` pipeline is part of the logged model; the API loads the registry `@champion` and uses the same code (Phases 4-5) | Done (Phase 5) |
| 6 | FastAPI app with a `/predict` endpoint | `services/api` serving the MLflow champion, hot reload | Done (Phase 5) |
| 7 | Run the API locally (uvicorn or Docker) | `make up` (Docker, hardened) | Done (Phase 1) |
| 8 | Send a sample request | `sample-request.json` + `curl` (README); answer 20.99 min from model v2 | Done (Phase 5) |
| 9 | Document RMSE and follow-up ideas in the README | RMSE in the README (Phase 4); follow-up ideas in Phase 7 | RMSE done |

## Stretch

| Stretch goal | Where | Status |
|---|---|---|
| Tests for pipeline, model, API | pytest (API + contracts now), dbt tests, model tests | Started (Phase 1) |
| Hyperparameter tuning with GridSearchCV or Optuna | Optuna (TPE, 30 trials) tuning LightGBM on the validation week | Done (Phase 4) |
| Compare tuned vs baseline and judge whether it was worth it | Tuned LightGBM 4.29 vs RandomForest 4.52 min: 5% better and better on every slice; worth it (training ~30 min on a laptop) | Done (Phase 4) |

## Questions to answer

1. What is the RMSE of your final model?
2. What would you do differently if you had more time?
