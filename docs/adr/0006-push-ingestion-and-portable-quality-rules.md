# ADR-0006: Push-based ingestion and engine-portable quality rules

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

1. **Databricks Free Edition restricts outbound internet** from serverless compute to an
   unpublished allowlist of trusted domains. Whether the NYC TLC download host is on it
   is unknown and may differ by region, so a pipeline that downloads from inside
   Databricks could break without warning.
2. **The quality rules are the heart of the silver layer** and must be testable on every
   commit. Running Spark in CI is slow and does not support Databricks-only features
   (expectations, Auto Loader, Unity Catalog).

## Decision

**Ingestion is pushed, not pulled.** `jobs/ingest` runs outside Databricks (laptop now,
Airflow from Phase 3). It downloads a TLC month, verifies it against the raw contract
(`RAW_YELLOW_REQUIRED`: required columns by *type family*, since the TLC switches
int32/int64 between months), writes a manifest (SHA-256, rows, columns), versions it with
DVC, and uploads it to the Unity Catalog landing volume. Auto Loader picks it up from
there exactly once. Re-runs are idempotent (checksum match = no work).

**Quality rules are portable SQL in plain Python.** `lakehouse_rules` holds the silver
SELECT and each rule as a SQL boolean expression with the EDA evidence behind it. The
expressions use only constructs that behave the same in Spark SQL (ANSI mode) and
DuckDB, and never divide by a column (ANSI raises on divide-by-zero). The Lakeflow
pipeline applies them with `@dp.expect_all_or_drop`; tests apply the *same objects* with
DuckDB, including on the real 3.48M-row month.

The gold layer is dbt with an enforced contract. It runs on Databricks in production and
on DuckDB in CI; the one engine difference that matters (day-of-week numbering) is
isolated in an adapter-dispatched macro and pinned by a dbt unit test.

## Consequences

- One rule definition, two engines: the real-data test reproduces the EDA funnel exactly
  (3,475,226 raw → 3,412,187 de-duplicated → 3,312,645 silver → 3,287,442 gold).
- The whole medallion flow can be rebuilt locally (`make lakehouse-local`) without a
  Databricks account, which keeps the project reviewable and CI fast.
- Rules are limited to the shared SQL subset. A rule that needs engine-specific SQL must
  get a dialect switch like the one for the duration expression in `silver.py`.
- Lakeflow records dropped-row counts per rule in its event log, giving data-quality
  observability in production for free.
