# Developer entry points. Every target is a thin wrapper, so CI and humans run
# exactly the same commands. `make help` lists them.
.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose -f infra/compose/compose.yaml

.PHONY: help setup hooks lint format typecheck test check image up down logs smoke clean

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

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

check: lint typecheck test ## Everything CI runs, except the image build

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
