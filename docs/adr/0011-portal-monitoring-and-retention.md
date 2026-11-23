# ADR-0011: Department portal from one published snapshot, drift monitoring, image retention

- **Status:** Accepted
- **Date:** 2026-11-23

## Context

The owner wants to see the whole platform the way a company would: an overview and one view per department. The departments are requirements, data engineering, analytics/BI, data science, DevOps, MLOps, and evaluation and monitoring. Each view should show its tools, its practices and live numbers.

The public site must stay small, strict and cheap:
- the CSP allows same-origin code only;
- the services scale to zero;
- Airflow, MLflow and Databricks stay private.

The model also needs a check that it still fits newer data. Old container images must not pile up in the registry or on the server.

## Decision

**1. One published snapshot, not live connections.** At the end of every monthly training run, the job `taxi-portal-snapshot` collects each department's numbers from its own source of record:

| Section | Source |
|---|---|
| Data engineering | Databricks SQL: row counts per layer and month; per-rule pass/fail from the Lakeflow event log (`event_log()`; the REST event list omits these details) |
| Analytics | dbt marts `bi_hourly_demand`, `bi_daily_trips`, `bi_pickup_zones` on gold |
| Data science, MLOps | MLflow registry: versions, aliases, metrics, slices, gate results |
| Monitoring | the latest drift report in MLflow |
| DevOps | GitHub Actions public API: recent CI and CD runs |

The job then publishes this as one MLflow artifact (`portal.json`, experiment `taxi-portal`). Each collector fails on its own: a section whose source is down is marked `unavailable`, and the rest is still published.

The API serves the snapshot read-only at `GET /portal`:
- it refreshes it in the same background loop as the model;
- it caches a copy next to the model, so a cold start shows it immediately while MLflow sleeps.

nginx allows only this one extra GET route.

The browser therefore never talks to Databricks, MLflow or GitHub. The portal needs no new credentials, and costs nothing between runs.

**2. Static facts stay static.** The requirements register (`docs/requirements.yaml`) and the model card (`docs/model-card.md`) are the sources of truth. `tools/build_portal_static.py` copies them into the site. Two tests enforce this:
- tests fail if the copies drift from the sources;
- tests fail if a requirement points at a file or test that does not exist, which makes traceability a CI check.

**3. The UI is hand-built.**
- Plain DOM and SVG, no chart library, no third-party code.
- All text is inserted with `textContent`.
- Bar widths are set through the CSSOM, so the strict CSP stays unchanged.
- Hash routing keeps it a static site.
- Colours come from theme variables, so light and dark mode both work.

**4. Drift monitoring.** `taxi-monitor` compares the new month with the month before:
- PSI on distance, hour, weekday and both zones (zones: top 30 plus "other"), and on the target;
- the champion's RMSE on the new month.

The thresholds are:
- PSI below 0.1 is stable, 0.1 to 0.25 is moderate, above 0.25 is significant;
- retraining is recommended when drift is significant or RMSE is more than 10% worse.

The report is logged to MLflow (`taxi-monitoring`). In Airflow, `monitor_drift` and `publish_portal_snapshot` run after the gate with `trigger_rule=all_done`, so a rejected challenger still produces a report.

**5. Image retention.**
- **GHCR:** a weekly workflow keeps the 10 newest *tagged* images per package. It uses our own small script (`tools/ghcr_cleanup.py`, unit-tested), not a generic "keep N versions" action. Each push also creates untagged child manifests (the platform image and its provenance attestation), and counting those would delete parts of the images we keep.
- **Server:** a weekly systemd timer keeps every image a container uses plus the 2 newest per `taxi-*` repository, so the previous release stays available for rollback. Sleeping containers still count as in use.
- **Laptop:** `make docker-clean` runs the same script.

## Consequences

- The portal is at most one month old for pipeline numbers. This is the right granularity: the data itself is monthly. CI runs are as fresh as the last snapshot.
- Adding a department means adding one collector and one renderer, behind the same contract (`snapshot_version`).
- First real run: February 2025 against January.
  - All PSI values are below 0.02 (stable).
  - The champion's RMSE is 4.29 on its own test week and 4.52 on February, which is 5.3% worse.
  - Retraining is not recommended.
- The GHCR clean-up needs the packages to be linked to this repository: CD publishes them with the repository's token, so they are.
