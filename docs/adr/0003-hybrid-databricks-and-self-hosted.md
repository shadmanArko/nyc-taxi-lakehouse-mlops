# ADR-0003: Hybrid platform - Databricks for the lakehouse, self-hosted for the rest

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

- Databricks (Delta Lake, Unity Catalog, Lakeflow Declarative Pipelines) is the industry
  standard lakehouse stack and the most relevant one to demonstrate. The **Free Edition** is
  available: serverless only, with usage limits.
- A self-hosted Linux server (8 cores, ~22 GiB RAM, 6 GiB GPU) is available for long-running
  services. It is private: nothing on the public internet can reach it directly.
- The course brief requires local MLflow tracking and a FastAPI service.

## Decision

- **Databricks Free Edition** owns the medallion layers:
  - **bronze → silver:** Lakeflow Declarative Pipelines (formerly Delta Live Tables), with
    expectations for data quality (drop/quarantine invalid trips).
  - **silver → gold:** dbt (`dbt-databricks`) on a serverless SQL warehouse, for business
    logic, tests and generated docs. Lakeflow handles ingestion well; dbt is the standard for
    tested, documented SQL modeling.
- **The server** owns everything stateful or long-running: object storage, DVC remote, Airflow,
  MLflow, the training job, the API, the web UI and monitoring.
- Data crosses the boundary **outbound only**: an Airflow task on the server triggers the
  Databricks job, then reads the gold table via the Databricks SQL connector and snapshots it
  with DVC into object storage. Training reads only from that snapshot.

## Consequences

- No inbound access to the server is needed for the data path.
- Training is reproducible without Databricks being available: the DVC snapshot is the input.
- Free Edition limits (serverless only, quotas) cap data volume per run; one month of yellow
  taxi data (~3-4M rows) fits comfortably.
- If Databricks is unavailable or the free tier changes, the gold contract lets a local engine
  (e.g. DuckDB + dbt-duckdb) replace it without touching training or serving.
