## What and why

<!-- One or two sentences. Link the roadmap phase or issue. -->

## Type

- [ ] feat  - [ ] fix  - [ ] data/pipeline  - [ ] model  - [ ] infra/CI  - [ ] docs

## Checklist

- [ ] `make check` passes locally (hooks, mypy, tests)
- [ ] Contracts unchanged, or `CONTRACT_VERSION` bumped and consumers updated
- [ ] No secrets, data files or model artifacts committed (data goes through DVC)
- [ ] Docs/ADR updated if a design decision changed
- [ ] Server changes (if any) are written as a deployment request, not applied

## Evidence

<!-- Test output, metrics (RMSE), screenshots of MLflow/Airflow, curl output. -->
