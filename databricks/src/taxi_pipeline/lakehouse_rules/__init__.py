"""Engine-agnostic silver-layer logic for the Lakeflow pipeline.

Everything here is plain Python and portable SQL, so the *same* rules run in the
Databricks pipeline (Spark SQL) and in local tests (DuckDB) against the real
January 2025 file. Nothing in this package imports Spark.
"""

from lakehouse_rules.quality import DROP_RULES, WARN_RULES, Rule
from lakehouse_rules.silver import Dialect, silver_select_sql

__all__ = ["DROP_RULES", "WARN_RULES", "Dialect", "Rule", "silver_select_sql"]
