# Assignment brief and requirement mapping

Origin: a model-deployment course project. Train a regression model that predicts the
duration of NYC yellow taxi trips (January 2025 data), track experiments locally with MLflow,
and serve predictions with FastAPI.

## Required

| # | Requirement | Where it is met | Status |
|---|---|---|---|
| 1 | Load and clean January 2025 yellow taxi data; target `duration` | EDA notebook, ingest job, Lakeflow bronze/silver, dbt gold (`duration_min`) | Done (Phase 2) |
| 2 | Train/validation split | Training job (Phase 4) | Planned |
| 3 | Train a scikit-learn `RandomForestRegressor` | Training job, baseline model (Phase 4) | Planned |
| 4 | Evaluate with RMSE, log runs to MLflow | Training job + MLflow (Phase 4) | Planned |
| 5 | Reusable preprocessing and model loading for training and API | One sklearn pipeline (preprocessing + model) logged to MLflow; the API loads it from the registry (`champion` alias), and request/response shapes come from `libs/contracts` (Phases 4-5) | Planned |
| 6 | FastAPI app with a `/predict` endpoint | `services/api` | Skeleton done (Phase 1), model in Phase 5 |
| 7 | Run the API locally (uvicorn or Docker) | `make up` (Docker, hardened) | Done (Phase 1) |
| 8 | Send a sample request | `sample-request.json` + `curl` (Phase 5) | Planned |
| 9 | Document RMSE and follow-up ideas in the README | README "Assignment" table (Phases 4, 7) | Planned |

## Stretch

| Stretch goal | Where | Status |
|---|---|---|
| Tests for pipeline, model, API | pytest (API + contracts now), dbt tests, model tests | Started (Phase 1) |
| Hyperparameter tuning with GridSearchCV or Optuna | Optuna in the training job (Phase 4) | Planned |
| Compare tuned vs baseline and judge whether it was worth it | Write-up in README (Phase 4) | Planned |

## Questions to answer

1. What is the RMSE of your final model?
2. What would you do differently if you had more time?
