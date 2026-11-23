# Developer entry points. Every target is a thin wrapper, so CI and humans run
# exactly the same commands. `make help` lists them.
.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose -f infra/compose/compose.yaml

.PHONY: help setup hooks lint format typecheck test check eda data-pull ingest ingest-upload \
	dbt-ci lakehouse-local bundle-validate bundle-deploy bundle-run mlflow-up mlflow-down train promote \
	airflow-test airflow-bootstrap \
	airflow-up airflow-down airflow-run image up gateway-up gateway-down wake down logs smoke clean \
	monitor portal-snapshot portal-static docker-clean

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install all workspace packages + dev tools, then git hooks
	uv sync --all-packages
	$(MAKE) hooks

hooks: ## Install pre-commit, commit-msg and pre-push hooks
	uv run pre-commit install

lint: ## Run every pre-commit hook on the whole repo (as CI does)
	uv run pre-commit run --all-files

format: ## Auto-format and auto-fix Python
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Strict type check
	uv run mypy

test: ## Tests with coverage gate
	uv run pytest --cov

check: lint typecheck test dbt-ci ## Everything CI runs, except the image build

eda: ## Re-execute the EDA notebooks from their .py sources (needs data/ pulled)
	uv sync --all-packages --group eda
	cd notebooks && for f in *.py; do \
		uv run jupytext --to ipynb "$$f" && \
		uv run jupyter nbconvert --to notebook --execute --inplace "$${f%.py}.ipynb"; \
	done

# --------------------------------------------------------------------------- #
# Data platform (Phase 2)                                                      #
# --------------------------------------------------------------------------- #
MONTH ?= 2025-01
BUNDLE_TARGET ?= dev
# Landing path as the bundle actually resolves it (dev prefixes schemas with dev_<user>_).
LANDING_VOLUME ?= $(shell databricks bundle summary -t $(BUNDLE_TARGET) -o json 2>/dev/null | \
	python3 -c "import json,sys; d=json.load(sys.stdin); r=d['resources']; \
	print('/Volumes/%s/%s/landing/yellow' % (d['variables']['catalog']['value'], r['schemas']['raw']['name']))")
# Each recipe line is one shell: enter the dbt project once, then chain commands.
IN_DBT := cd transform/dbt && export DBT_PROFILES_DIR=. && uv sync -q &&

data-pull: ## Fetch versioned data from the DVC remote
	uv sync --all-packages --group data && uv run dvc pull

ingest: ## Download + verify one TLC month (MONTH=2025-01)
	uv run taxi-ingest --month $(MONTH)

ingest-upload: ## ...and push it to the Databricks landing volume (needs Databricks auth)
	uv run taxi-ingest --month $(MONTH) --upload --volume $(LANDING_VOLUME)

dbt-ci: ## dbt on DuckDB fixtures: contract, data tests, unit tests (as CI)
	$(IN_DBT) uv run dbt deps && uv run dbt seed --target ci \
		&& uv run dbt build --target ci --exclude resource_type:seed

lakehouse-local: ## Full medallion flow on the real data, locally on DuckDB
	uv run python tools/local_lakehouse.py data/raw/tlc/yellow/*.parquet
	$(IN_DBT) uv run dbt deps && DBT_DUCKDB_PATH=../../target/local_lakehouse.duckdb \
		uv run dbt build --target ci --exclude resource_type:seed --vars '{min_gold_rows: 3000000}'

bundle-validate: ## Validate the Databricks bundle (needs Databricks auth)
	databricks bundle validate -t $(BUNDLE_TARGET) --strict

bundle-deploy: ## Deploy schemas, volume, pipeline and job
	databricks bundle deploy -t $(BUNDLE_TARGET)

bundle-run: ## Run the lakehouse refresh job (bronze -> silver -> gold)
	databricks bundle run taxi_medallion_refresh -t $(BUNDLE_TARGET)

# --------------------------------------------------------------------------- #
# Machine learning (Phase 4)                                                   #
# --------------------------------------------------------------------------- #
MLFLOW := docker compose -f infra/mlflow/compose.yaml
# Training reads the snapshot from object storage via the taxi-dvc AWS profile + tunnel.
# Server MLflow through the SSH tunnel (Mac 15050 -> server 5050). Local MLflow: 5050.
MLFLOW_URI ?= http://127.0.0.1:15050
TRAIN_ENV := MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_TRACKING_URI=$(MLFLOW_URI) \
	AWS_PROFILE=taxi-dvc TAXI_S3_ENDPOINT_URL=http://127.0.0.1:8333
MODEL ?= lightgbm
TRIALS ?= 30

mlflow-up: ## Start local MLflow at http://127.0.0.1:5050
	$(MLFLOW) up -d --build --wait

mlflow-down: ## Stop local MLflow
	$(MLFLOW) down

train: ## Train MODEL (mean_baseline|random_forest|lightgbm) on MONTH, register as challenger
	$(TRAIN_ENV) uv run taxi-train --month $(MONTH) --model $(MODEL) --tune-trials $(TRIALS) --register

promote: ## Evaluation gate: promote @challenger to @champion if it is better
	$(TRAIN_ENV) uv run taxi-promote --month $(MONTH)

# --------------------------------------------------------------------------- #
# Monitoring + portal (Phase 7)                                                #
# --------------------------------------------------------------------------- #
monitor: ## Drift report: REF month vs MONTH (PSI + champion RMSE), logged to MLflow
	$(TRAIN_ENV) uv run taxi-monitor --reference-month $(REF) --current-month $(MONTH)

# Databricks: CLI profile `taxi`; the warehouse id comes from the environment. For the dev
# bundle pass its prefix, e.g. SCHEMA_PREFIX=dev_<user>_ PIPELINE_NAME="[dev <user>] taxi-medallion".
REF ?= 2025-01
WAREHOUSE_ID ?= $(DATABRICKS_WAREHOUSE_ID)
SCHEMA_PREFIX ?=
PIPELINE_NAME ?= taxi-medallion
DRY_RUN ?= 0

portal-snapshot: ## Collect every department's numbers and publish the portal snapshot
	DATABRICKS_CONFIG_PROFILE=taxi DATABRICKS_WAREHOUSE_ID=$(WAREHOUSE_ID) \
		MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_TRACKING_URI=$(MLFLOW_URI) uv run taxi-portal-snapshot \
		--bronze-schema $(SCHEMA_PREFIX)taxi_bronze --silver-schema $(SCHEMA_PREFIX)taxi_silver \
		--gold-schema $(SCHEMA_PREFIX)taxi_gold --pipeline-name "$(PIPELINE_NAME)"

portal-static: ## Regenerate the portal's static data (requirements register, model card)
	uv run python tools/build_portal_static.py

# --------------------------------------------------------------------------- #
# Orchestration (Phase 3)                                                      #
# --------------------------------------------------------------------------- #
AIRFLOW := docker compose -f orchestration/airflow/compose.yaml
AIRFLOW_TEST_HOME := $(CURDIR)/target/airflow-test-home

airflow-test: ## DAG integrity tests (own uv project)
	mkdir -p $(AIRFLOW_TEST_HOME)
	cd orchestration/airflow && uv sync -q && AIRFLOW_HOME=$(AIRFLOW_TEST_HOME) uv run pytest -q

airflow-bootstrap: ## Create local .env + connections file (asks for a Databricks token)
	BUNDLE_TARGET=$(BUNDLE_TARGET) uv run --no-project python orchestration/airflow/bootstrap_local.py

airflow-up: ## Start local Airflow at http://127.0.0.1:8080
	$(AIRFLOW) up -d --build --wait

airflow-down: ## Stop local Airflow
	$(AIRFLOW) down

airflow-run: ## Trigger the monthly refresh for MONTH (default 2025-01)
	$(AIRFLOW) exec airflow-scheduler airflow dags unpause taxi_monthly_refresh
	$(AIRFLOW) exec airflow-scheduler airflow dags trigger taxi_monthly_refresh -c '{"month": "$(MONTH)"}'

# --------------------------------------------------------------------------- #
# Serving                                                                      #
# --------------------------------------------------------------------------- #
image: ## Build the API image for the server's platform (linux/amd64)
	docker buildx build --platform linux/amd64 -f services/api/Dockerfile -t taxi-api:local --load .

up: ## Start the local stack
	$(COMPOSE) up -d --build --wait

GATEWAY := docker compose -f infra/gateway/compose.yaml

gateway-up: ## Scale-to-zero gateway at http://127.0.0.1:8090 (sleeps web/api/mlflow)
	$(GATEWAY) up -d --build --wait

gateway-down: ## Stop the gateway (services stay as they are)
	$(GATEWAY) down

wake: ## Wake the serving group through the gateway and time it
	curl -s -o /dev/null -w 'woke in %{time_total}s (HTTP %{http_code})\n' \
		-X POST http://127.0.0.1:8090/api/predict -H 'Content-Type: application/json' -d @sample-request.json

down: ## Stop the local stack
	$(COMPOSE) down

logs: ## Follow local stack logs
	$(COMPOSE) logs -f

smoke: ## Hit the running API: health, ready (model version), one prediction
	curl -fsS http://127.0.0.1:$${API_HOST_PORT:-8000}/health && echo
	curl -sS http://127.0.0.1:$${API_HOST_PORT:-8000}/ready && echo
	curl -sS -X POST http://127.0.0.1:$${API_HOST_PORT:-8000}/predict \
		-H 'Content-Type: application/json' -d @sample-request.json && echo

docker-clean: ## Free disk: old taxi-* images (keeps in-use + 2 newest), dangling layers, old cache
	DRY_RUN=$(DRY_RUN) bash infra/server/prune-images.sh

clean: ## Remove caches and build output
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml junit.xml htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
