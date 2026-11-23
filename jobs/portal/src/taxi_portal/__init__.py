"""Portal snapshot: one JSON document with every department's live numbers.

Built at the end of each monthly run and published as an MLflow artifact (experiment
``taxi-portal``), which the API already reads; the web portal only ever reads it. The
portal therefore never talks to Databricks, MLflow or GitHub directly (ADR-0011).
"""
