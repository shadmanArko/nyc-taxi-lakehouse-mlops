# ADR-0002: Monorepo with contract-based coupling

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

The system has independent parts (lakehouse transforms, orchestration, training, serving,
UI). We want them loosely coupled, yet one portfolio repository that is easy to review, with
one CI pipeline and consistent tooling.

## Decision

- One Git repository, organised as a **uv workspace**. Each Python component is its own
  package with its own `pyproject.toml`, dependencies and (if it ships) Dockerfile:
  `libs/*` for shared libraries, `services/*` for long-running services, `jobs/*` for batch
  jobs (added when the first job exists).
- **Components never import each other.** The only shared code is `libs/contracts`
  (Pydantic schemas, semantically versioned). Other boundaries are data contracts: the gold
  table schema and the MLflow registry alias `champion`.
- One `uv.lock` for the workspace, so every component is tested against the same pinned
  versions; each image installs only its own package (`uv sync --package <name>`).
- Non-Python components (dbt, Databricks bundle, Airflow DAGs) live in their own top-level
  folders with their own tooling.

## Consequences

- Any component can be rebuilt, redeployed or rewritten alone, as long as its contract holds.
- A single lockfile means a dependency bump is tested across all components at once; the cost
  is that two components cannot pin conflicting versions of the same library. If that ever
  happens, the component moves out of the workspace (it already has its own pyproject).
- Contract changes are explicit: the version constant and tests make breaking changes visible.
