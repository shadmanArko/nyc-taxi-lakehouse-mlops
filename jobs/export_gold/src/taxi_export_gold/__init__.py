"""Export one month of gold as an immutable, content-addressed snapshot.

The lakehouse (Databricks) cannot reach the private server, so the server pulls:
query gold for one ``source_month`` -> check the gold contract -> write a
deterministic parquet file -> upload it under its own SHA-256 to object storage.
Training reads a snapshot *by hash* and records that hash in MLflow (ADR-0007).
"""
