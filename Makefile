# Developer entry points. Every target is a thin wrapper, so CI and humans run
# exactly the same commands. `make help` lists them.
.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose -f infra/compose/compose.yaml

.PHONY: help setup hooks lint format typecheck test check eda data-pull ingest ingest-upload \
	dbt-ci lakehouse-local bundle-validate bundle-deploy bundle-run image up down logs smoke clean

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
# Serving                                                                      #
# --------------------------------------------------------------------------- #
image: ## Build the API image for the server's platform (linux/amd64)
	docker buildx build --platform linux/amd64 -f services/api/Dockerfile -t taxi-api:local --load .

up: ## Start the local stack
	$(COMPOSE) up -d --build --wait

down: ## Stop the local stack
	$(COMPOSE) down

logs: ## Follow local stack logs
	$(COMPOSE) logs -f

smoke: ## Hit the running API
	curl -fsS http://127.0.0.1:$${API_HOST_PORT:-8000}/health && echo
	curl -sS -o /dev/null -w 'ready: HTTP %{http_code}\n' http://127.0.0.1:$${API_HOST_PORT:-8000}/ready

clean: ## Remove caches and build output
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml junit.xml htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
