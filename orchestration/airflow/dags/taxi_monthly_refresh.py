"""Monthly lakehouse refresh: TLC file -> landing volume -> Databricks -> gold snapshot.

    resolve_month -> fetch_tlc_month -> upload_to_landing -> refresh_lakehouse -> export_gold

Airflow only orchestrates. Each step is a self-contained job with its own CLI and
its own virtualenv (/opt/taxi-jobs), so Airflow's dependencies and ours never mix,
and every step can be run by hand exactly as Airflow runs it.

Credentials come from Airflow connections (a secrets backend), never from this file:
  databricks_default  host + personal access token (Databricks provider + job CLIs)
  taxi_storage        S3 access key / secret, endpoint in extra.endpoint_url
"""

import os
import re
from datetime import UTC, datetime, timedelta

from airflow.providers.databricks.operators.databricks import DatabricksRunNowOperator
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param, task

# Deployment-specific names (set in the container environment, see compose.yaml).
JOBS_BIN = "/opt/taxi-jobs/bin"
DATA_DIR = os.environ.get("TAXI_DATA_DIR", "/opt/airflow/data/raw/tlc/yellow")
LANDING_VOLUME = os.environ.get("TAXI_LANDING_VOLUME", "/Volumes/workspace/taxi_raw/landing/yellow")
DATABRICKS_JOB = os.environ.get("TAXI_DATABRICKS_JOB_NAME", "taxi-medallion-refresh")
CATALOG = os.environ.get("TAXI_CATALOG", "workspace")
GOLD_SCHEMA = os.environ.get("TAXI_GOLD_SCHEMA", "taxi_gold")
SNAPSHOT_BUCKET = os.environ.get("TAXI_SNAPSHOT_BUCKET", "taxi-dvc")
# Monthly in production. Local stacks set "none" (manual runs only): otherwise unpausing
# the DAG immediately creates a run for the latest schedule interval.
_SCHEDULE = os.environ.get("TAXI_DAG_SCHEDULE", "0 6 15 * *")  # 15th, 06:00 UTC
SCHEDULE = None if _SCHEDULE.lower() == "none" else _SCHEDULE

# The TLC publishes a month roughly two months after it ends.
PUBLICATION_LAG_MONTHS = 2
MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")

MONTH = "{{ ti.xcom_pull(task_ids='resolve_month') }}"
DATABRICKS_ENV = {
    "DATABRICKS_HOST": "{{ conn.databricks_default.host }}",
    "DATABRICKS_TOKEN": "{{ conn.databricks_default.password }}",
}
STORAGE_ENV = {
    "AWS_ACCESS_KEY_ID": "{{ conn.taxi_storage.login }}",
    "AWS_SECRET_ACCESS_KEY": "{{ conn.taxi_storage.password }}",
    "TAXI_S3_ENDPOINT_URL": "{{ conn.taxi_storage.extra_dejson.endpoint_url }}",
}


def month_before(run_after: datetime, months: int) -> str:
    """``YYYY-MM`` of the month ``months`` before ``run_after``'s month."""
    index = run_after.year * 12 + (run_after.month - 1) - months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


with DAG(
    dag_id="taxi_monthly_refresh",
    description="Ingest a TLC month, refresh bronze/silver/gold on Databricks, snapshot gold.",
    schedule=SCHEDULE,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,  # the lakehouse job allows one run at a time anyway
    tags=["taxi", "lakehouse", "phase-3"],
    params={
        "month": Param(
            None,
            type=["null", "string"],
            pattern=r"^20\d{2}-(0[1-9]|1[0-2])$",
            description="YYYY-MM to process. Empty = two months before the run date.",
        ),
    },
    default_args={
        "owner": "data-platform",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
        "execution_timeout": timedelta(minutes=30),
    },
    doc_md=__doc__,
) as dag:

    @task
    def resolve_month(params: dict[str, object] | None = None, dag_run: object = None) -> str:
        """Pick the month: explicit param, else two months before the run date."""
        requested = (params or {}).get("month")
        if requested:
            month = str(requested)
        else:
            run_after = getattr(dag_run, "run_after", None) or datetime.now(UTC)
            month = month_before(run_after, PUBLICATION_LAG_MONTHS)
        if not MONTH_RE.match(month):
            msg = f"invalid month {month!r}"
            raise ValueError(msg)
        return month

    fetch = BashOperator(
        task_id="fetch_tlc_month",
        doc_md="Download the TLC file and verify it against the raw contract (idempotent).",
        bash_command=f"{JOBS_BIN}/taxi-ingest --month {MONTH} --data-dir {DATA_DIR}",
    )

    upload = BashOperator(
        task_id="upload_to_landing",
        doc_md="Upload the verified file + manifest to the Unity Catalog landing volume.",
        bash_command=(
            f"{JOBS_BIN}/taxi-ingest --month {MONTH} --data-dir {DATA_DIR} "
            f"--upload --volume {LANDING_VOLUME}"
        ),
        env=DATABRICKS_ENV,
        append_env=True,
    )

    refresh = DatabricksRunNowOperator(
        task_id="refresh_lakehouse",
        doc_md="Run the bundle's job: Lakeflow bronze/silver, then dbt gold with its tests.",
        databricks_conn_id="databricks_default",
        job_name=DATABRICKS_JOB,
        deferrable=True,  # waits in the triggerer, not in a worker slot
        execution_timeout=timedelta(hours=1),
    )

    export = BashOperator(
        task_id="export_gold",
        doc_md="Check gold against its contract and publish a content-addressed snapshot.",
        bash_command=(
            f"{JOBS_BIN}/taxi-export-gold --month {MONTH} --catalog {CATALOG} "
            f"--schema {GOLD_SCHEMA} --bucket {SNAPSHOT_BUCKET}"
        ),
        env={
            **DATABRICKS_ENV,
            **STORAGE_ENV,
            "DATABRICKS_WAREHOUSE_ID": "{{ conn.databricks_default.extra_dejson.warehouse_id }}",
        },
        append_env=True,
    )

    resolve_month() >> fetch >> upload >> refresh >> export
