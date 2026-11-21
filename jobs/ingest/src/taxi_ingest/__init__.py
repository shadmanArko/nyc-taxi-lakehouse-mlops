"""Fetch NYC TLC monthly trip files, verify them, and land them in the lakehouse.

Why this runs outside Databricks: Free Edition serverless compute only reaches an
unpublished allowlist of domains, so the lakehouse cannot be relied on to download
from the TLC itself. Data is *pushed* into a Unity Catalog volume instead (the
"landing zone"), and Auto Loader takes it from there. See ADR-0006.
"""
