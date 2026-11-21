"""DAG integrity tests: import cleanly, correct shape, safe defaults, no secrets."""

import itertools
from datetime import UTC, datetime
from pathlib import Path

import pytest
from airflow.dag_processing.dagbag import DagBag

DAGS = Path(__file__).parents[1] / "dags"


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    return DagBag(dag_folder=str(DAGS))


def test_dags_import_without_errors(dagbag: DagBag) -> None:
    assert dagbag.import_errors == {}
    assert "taxi_monthly_refresh" in dagbag.dags


def test_task_order(dagbag: DagBag) -> None:
    dag = dagbag.dags["taxi_monthly_refresh"]
    order = [
        "resolve_month",
        "fetch_tlc_month",
        "upload_to_landing",
        "refresh_lakehouse",
        "export_gold",
    ]
    assert set(dag.task_ids) == set(order)
    for upstream, downstream in itertools.pairwise(order):
        assert dag.get_task(downstream).upstream_task_ids == {upstream}


def test_safe_scheduling_defaults(dagbag: DagBag) -> None:
    dag = dagbag.dags["taxi_monthly_refresh"]
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    for task in dag.tasks:
        assert task.retries >= 2, task.task_id
        assert task.execution_timeout is not None, task.task_id


def test_production_schedule_is_monthly(dagbag: DagBag) -> None:
    """Default (no TAXI_DAG_SCHEDULE) is the monthly cron; local compose overrides it."""
    assert dagbag.dags["taxi_monthly_refresh"].schedule == "0 6 15 * *"


def test_databricks_step_is_deferrable(dagbag: DagBag) -> None:
    task = dagbag.dags["taxi_monthly_refresh"].get_task("refresh_lakehouse")
    assert task.deferrable is True


def test_no_secrets_in_dag_source() -> None:
    source = (DAGS / "taxi_monthly_refresh.py").read_text()
    # Built from parts so this test file itself does not look like it contains a key.
    for marker in ("dapi", "aws_secret_access_key =", "BEGIN " + "PRIVATE KEY"):
        assert marker not in source


@pytest.mark.parametrize(
    ("run_after", "expected"),
    [
        (datetime(2026, 3, 15, tzinfo=UTC), "2026-01"),
        (datetime(2026, 1, 15, tzinfo=UTC), "2025-11"),
        (datetime(2026, 2, 1, tzinfo=UTC), "2025-12"),
        (datetime(2026, 12, 31, tzinfo=UTC), "2026-10"),
    ],
)
def test_month_resolution_crosses_years(run_after: datetime, expected: str) -> None:
    from taxi_monthly_refresh import month_before

    assert month_before(run_after, 2) == expected
