"""Train a challenger on the newest gold snapshot, then run the promotion gate.

    resolve_month -> train_challenger -> promotion_gate

Scheduled by data, not by time: it runs whenever ``taxi_monthly_refresh`` publishes a
gold snapshot (asset event on GOLD_SNAPSHOTS, carrying the month). It can also be
triggered by hand with ``{"month": "YYYY-MM"}``.

The gate exits 3 when the challenger is evaluated correctly but is not better than the
champion; that shows as *skipped* (a normal outcome), not as a failure.
"""

import os
import re
from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param, task
from common.assets import GOLD_SNAPSHOTS

JOBS_BIN = "/opt/taxi-jobs/bin"
MLFLOW_URI = os.environ.get("TAXI_MLFLOW_TRACKING_URI", "http://mlflow:5000")
SNAPSHOT_BUCKET = os.environ.get("TAXI_SNAPSHOT_BUCKET", "taxi-dvc")
TUNE_TRIALS = int(os.environ.get("TAXI_TUNE_TRIALS", "20"))
MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
NOT_PROMOTED_EXIT_CODE = 3

MONTH = "{{ ti.xcom_pull(task_ids='resolve_month') }}"
JOB_ENV = {
    "MLFLOW_TRACKING_URI": MLFLOW_URI,
    "MLFLOW_DISABLE_AGENT_HINT": "1",
    "AWS_ACCESS_KEY_ID": "{{ conn.taxi_storage.login }}",
    "AWS_SECRET_ACCESS_KEY": "{{ conn.taxi_storage.password }}",
    "TAXI_S3_ENDPOINT_URL": "{{ conn.taxi_storage.extra_dejson.endpoint_url }}",
    "TAXI_SNAPSHOT_BUCKET": SNAPSHOT_BUCKET,
}

with DAG(
    dag_id="taxi_train_and_promote",
    description="Train a tuned challenger on the latest gold snapshot and gate its promotion.",
    schedule=[GOLD_SNAPSHOTS],
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    tags=["taxi", "ml", "phase-4"],
    params={
        "month": Param(
            None,
            type=["null", "string"],
            pattern=r"^20\d{2}-(0[1-9]|1[0-2])$",
            description="YYYY-MM to train on. Empty = the month in the triggering asset event.",
        ),
    },
    default_args={
        "owner": "ml-platform",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
        "execution_timeout": timedelta(hours=2),
    },
    doc_md=__doc__,
) as dag:

    @task
    def resolve_month(
        params: dict[str, object] | None = None, triggering_asset_events: object = None
    ) -> str:
        """Explicit param first, else the month announced by the refresh DAG."""
        month = (params or {}).get("month")
        if not month and triggering_asset_events:
            events = triggering_asset_events[GOLD_SNAPSHOTS]  # type: ignore[index]
            month = events[-1].extra.get("month") if events else None
        if not month or not MONTH_RE.match(str(month)):
            msg = f"no valid month to train on (got {month!r})"
            raise ValueError(msg)
        return str(month)

    train = BashOperator(
        task_id="train_challenger",
        doc_md="Optuna-tuned LightGBM on the snapshot; registered as @challenger in MLflow.",
        bash_command=(
            f"{JOBS_BIN}/taxi-train --month {MONTH} --model lightgbm "
            f"--tune-trials {TUNE_TRIALS} --register"
        ),
        env=JOB_ENV,
        append_env=True,
    )

    gate = BashOperator(
        task_id="promotion_gate",
        doc_md="Re-score champion and challenger on the same test week; promote if better.",
        bash_command=f"{JOBS_BIN}/taxi-promote --month {MONTH}",
        env=JOB_ENV,
        append_env=True,
        skip_on_exit_code=NOT_PROMOTED_EXIT_CODE,
        retries=0,  # a rejection is a decision, not a transient failure
    )

    resolve_month() >> train >> gate
