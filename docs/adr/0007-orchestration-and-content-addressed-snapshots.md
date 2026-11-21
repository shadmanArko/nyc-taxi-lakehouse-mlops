# ADR-0007: Airflow orchestration and content-addressed gold snapshots

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

The monthly flow crosses three systems: the TLC website, Databricks and the self-hosted
server. It needs scheduling, retries, backfills by month, and a clear record of what
ran. Training (Phase 4) must be reproducible: a model has to be traceable to the exact
data it was trained on, even after gold is rebuilt.

## Decision

**Airflow 3.4 orchestrates; it does not compute.** One DAG, `taxi_monthly_refresh`:
`resolve_month → fetch_tlc_month → upload_to_landing → refresh_lakehouse → export_gold`.

- Each step is one of our job CLIs (`taxi-ingest`, `taxi-export-gold`) or the Databricks
  job. The CLIs live in their **own virtualenv in the image** (`/opt/taxi-jobs`), so
  Airflow's ~150 pinned packages and ours never meet, and every step can be re-run by
  hand exactly as Airflow runs it.
- The Databricks step uses the official provider with `deferrable=True`: while the
  lakehouse job runs (minutes), the wait sits in the triggerer, not a worker slot.
- Providers are installed with Airflow's **official constraints file** for 3.4.0, so the
  image uses the exact versions Airflow tested together. The DAG test environment is its
  own uv project (like dbt) for the same reason.
- Credentials come from Airflow connections via a file-based secrets backend (a mounted
  secret file), never from DAG code. A DAG test asserts the source holds no secrets.
- Safe defaults are enforced by tests: no catchup, one active run, retries with
  exponential backoff and a timeout on every task.

**Gold snapshots are immutable and content-addressed.** `taxi-export-gold` reads one
month of gold, checks it against the gold contract (columns, types, non-null target,
unique `trip_id`), writes a deterministic parquet file and publishes it as
`snapshots/gold/trip_features/source_month=YYYY-MM/<sha256>.parquet` plus a manifest,
then moves a `LATEST` pointer. Re-exporting identical data is a no-op.

We do **not** run DVC inside Airflow. DVC versions data through `.dvc` files committed
to Git, and an orchestrator committing to Git is fragile and needs write credentials.
DVC stays the developer tool for the repo's raw data; for orchestrated data the SHA-256
*is* the version, and Phase 4 records it on every MLflow run.

## Consequences

- Verified on real data: exporting January from Databricks produced 3,287,442 rows and a
  second export from a fresh warehouse read gave the same SHA-256 (nothing re-uploaded).
  The full DAG then ran end to end in Airflow (fetch, upload, Databricks job 161 s,
  export) and reproduced the identical snapshot hash.
- Airflow 3 gotchas found on the first run and fixed: tasks run in a separate worker
  process that reads `[workers] secrets_backend` (not only `[secrets] backend`); and the
  filesystem secrets backend chooses its parser from the file extension, so the Docker
  secret is mounted as `connections.json`. Unpausing a scheduled DAG immediately creates a
  run for the latest interval, so local stacks set `TAXI_DAG_SCHEDULE=none` (manual only).
- A model can always be traced to its exact training bytes, even if gold is rebuilt
  later: the snapshot under that hash never changes.
- The Airflow image is built for `linux/amd64` and vulnerability-scanned in CI like the
  API image. Its base keeps per-package dpkg records that `apt-get upgrade` leaves stale;
  the Dockerfile reconciles them so the scan reflects what is actually installed.
- Locally the UI has no login and Postgres uses a local password; the server deployment
  (Phase 6) needs real auth and file-based secrets.
