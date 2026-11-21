"""Contracts that span components, checked without importing across them.

The gold table is defined twice on purpose: in dbt (which enforces it in the
warehouse) and in libs/contracts (which training and serving read). These tests
fail if the two drift apart, or if the bundle stops wiring the layers the way the
code expects.
"""

from pathlib import Path
from typing import Any

import yaml

from taxi_contracts import GOLD_TARGET, GOLD_TRIP_FEATURES, GOLD_TRIP_FEATURES_TABLE

ROOT = Path(__file__).parents[1]
DBT_MODEL_YML = ROOT / "transform/dbt/models/gold/trip_features.yml"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def _gold_model() -> dict[str, Any]:
    models = _load(DBT_MODEL_YML)["models"]
    (model,) = [m for m in models if m["name"] == GOLD_TRIP_FEATURES_TABLE]
    return dict(model)


def test_dbt_gold_columns_and_types_match_python_contract() -> None:
    dbt_columns = {c["name"]: c["data_type"] for c in _gold_model()["columns"]}
    assert dbt_columns == GOLD_TRIP_FEATURES


def test_dbt_gold_column_order_matches_python_contract() -> None:
    assert [c["name"] for c in _gold_model()["columns"]] == list(GOLD_TRIP_FEATURES)


def test_dbt_gold_contract_is_enforced() -> None:
    assert _gold_model()["config"]["contract"]["enforced"] is True


def test_target_is_documented_as_never_a_feature() -> None:
    (target,) = [c for c in _gold_model()["columns"] if c["name"] == GOLD_TARGET]
    assert "never use as a feature" in target["description"].lower()


def test_bundle_wires_pipeline_config_to_declared_schemas() -> None:
    pipeline = _load(ROOT / "databricks/resources/pipeline.yml")["resources"]["pipelines"]
    config = pipeline["taxi_medallion"]["configuration"]
    assert config["taxi.bronze_schema"] == "${resources.schemas.bronze.name}"
    assert config["taxi.silver_schema"] == "${resources.schemas.silver.name}"
    # The keys the pipeline code reads must all be provided.
    for source in (ROOT / "databricks/src/taxi_pipeline/transformations").glob("*.py"):
        keys = ("taxi.catalog", "taxi.bronze_schema", "taxi.silver_schema", "taxi.landing_path")
        for key in keys:
            if f'"{key}"' in source.read_text():
                assert key in config, f"{source.name} reads {key}, which the bundle never sets"


def test_bundle_dbt_task_reads_silver_from_the_pipeline_schema() -> None:
    job = _load(ROOT / "databricks/resources/job.yml")["resources"]["jobs"]
    dbt_task = next(t for t in job["taxi_medallion_refresh"]["tasks"] if "dbt_task" in t)
    build_cmd = " ".join(dbt_task["dbt_task"]["commands"])
    assert "silver_schema: ${resources.schemas.silver.name}" in build_cmd
    assert dbt_task["dbt_task"]["schema"] == "${resources.schemas.gold.name}"
