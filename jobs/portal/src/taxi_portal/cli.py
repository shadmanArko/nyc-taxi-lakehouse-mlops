"""``taxi-portal-snapshot``: collect every section and publish it to MLflow.

Credentials come only from the environment: Databricks SDK auth (+ DATABRICKS_WAREHOUSE_ID)
and MLFLOW_TRACKING_URI. GitHub is read anonymously (public repository).
"""

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from typing import Any

from taxi_portal.snapshot import (
    build_snapshot,
    collect_analytics,
    collect_data_engineering,
    collect_devops,
    collect_ml,
    collect_monitoring,
    pipeline_expectations,
    pipeline_id_by_name,
    safe,
)

EXPERIMENT = "taxi-portal"


def _cursor() -> Any:  # pragma: no cover - needs a live warehouse
    from databricks.sdk.core import Config

    from databricks import sql

    config = Config()
    connection = sql.connect(
        server_hostname=config.host.removeprefix("https://").rstrip("/"),
        http_path=f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}",
        credentials_provider=lambda: config.authenticate,
    )
    return connection.cursor()


def publish(snapshot: dict[str, Any]) -> str:
    import mlflow

    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"portal {snapshot['generated_at']}") as run:
        mlflow.set_tag("portal_snapshot", "true")
        mlflow.log_dict(snapshot, "portal.json")
        return str(run.info.run_id)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - wiring only
    parser = argparse.ArgumentParser(prog="taxi-portal-snapshot", description=__doc__)
    parser.add_argument("--catalog", default=os.environ.get("TAXI_CATALOG", "workspace"))
    parser.add_argument(
        "--bronze-schema", default=os.environ.get("TAXI_BRONZE_SCHEMA", "taxi_bronze")
    )
    parser.add_argument(
        "--silver-schema", default=os.environ.get("TAXI_SILVER_SCHEMA", "taxi_silver")
    )
    parser.add_argument("--gold-schema", default=os.environ.get("TAXI_GOLD_SCHEMA", "taxi_gold"))
    parser.add_argument(
        "--pipeline-name", default=os.environ.get("TAXI_PIPELINE_NAME", "taxi-medallion")
    )
    parser.add_argument("--repo", default="shadmanArko/nyc-taxi-lakehouse-mlops")
    parser.add_argument("--dry-run", action="store_true", help="print, do not publish")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import mlflow
    from databricks.sdk import WorkspaceClient

    workspace = WorkspaceClient()
    cursor = _cursor()
    client = mlflow.MlflowClient()
    ml_and_ops: dict[str, Any] = {}

    def ml_section() -> dict[str, Any]:
        ml, ops = collect_ml(client)
        ml_and_ops["mlops"] = ops
        return ml

    sections = {
        "data_engineering": safe(
            "data_engineering",
            lambda: collect_data_engineering(
                cursor,
                args.catalog,
                args.bronze_schema,
                args.silver_schema,
                args.gold_schema,
                lambda: pipeline_expectations(
                    cursor, pipeline_id_by_name(workspace, args.pipeline_name)
                ),
            ),
        ),
        "analytics": safe(
            "analytics", lambda: collect_analytics(cursor, args.catalog, args.gold_schema)
        ),
        "ml": safe("ml", ml_section),
        "monitoring": safe("monitoring", lambda: collect_monitoring(client)),
        "devops": safe("devops", lambda: collect_devops(args.repo)),
    }
    sections["mlops"] = ml_and_ops.get("mlops", {"status": "unavailable"})
    snapshot = build_snapshot(sections)
    if args.dry_run:
        print(json.dumps(snapshot, indent=2)[:4000])
        return 0
    run_id = publish(snapshot)
    statuses = {k: v.get("status") for k, v in snapshot.items() if isinstance(v, dict)}
    print(f"published portal snapshot run {run_id}: {statuses}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
