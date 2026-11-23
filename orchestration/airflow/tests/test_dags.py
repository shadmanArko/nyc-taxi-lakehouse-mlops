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
        "announce_gold_snapshot",
    ]
    assert set(dag.task_ids) == set(order)
    for upstream, downstream in itertools.pairwise(order):
        # the announcement also reads the month from resolve_month (XCom argument)
        assert upstream in dag.get_task(downstream).upstream_task_ids


@pytest.mark.parametrize("dag_id", ["taxi_monthly_refresh", "taxi_train_and_promote"])
def test_safe_scheduling_defaults(dagbag: DagBag, dag_id: str) -> None:
    dag = dagbag.dags[dag_id]
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    for task in dag.tasks:
        if task.task_id != "promotion_gate":  # a rejection is a decision, never retried
            assert task.retries >= 2, task.task_id
        assert task.execution_timeout is not None, task.task_id


def test_refresh_announces_the_gold_asset_that_schedules_training(dagbag: DagBag) -> None:
    from common.assets import GOLD_SNAPSHOTS

    announce = dagbag.dags["taxi_monthly_refresh"].get_task("announce_gold_snapshot")
    assert GOLD_SNAPSHOTS in announce.outlets
    train_dag = dagbag.dags["taxi_train_and_promote"]
    assert GOLD_SNAPSHOTS in train_dag.timetable.asset_condition.objects


def test_training_dag_shape_and_gate_semantics(dagbag: DagBag) -> None:
    dag = dagbag.dags["taxi_train_and_promote"]
    assert dag.get_task("train_challenger").upstream_task_ids == {"resolve_month"}
    gate = dag.get_task("promotion_gate")
    assert gate.upstream_task_ids == {"train_challenger"}
    assert gate.skip_on_exit_code == [3]  # not promoted = skipped, not failed
    assert gate.retries == 0


def test_monitoring_and_portal_run_whatever_the_gate_decides(dagbag: DagBag) -> None:
    dag = dagbag.dags["taxi_train_and_promote"]
    monitor = dag.get_task("monitor_drift")
    portal = dag.get_task("publish_portal_snapshot")
    assert {"promotion_gate", "previous_month"} <= monitor.upstream_task_ids
    assert portal.upstream_task_ids == {"monitor_drift"}
    assert monitor.trigger_rule == "all_done"
    assert portal.trigger_rule == "all_done"


@pytest.mark.parametrize(
    ("month", "shift", "expected"),
    [("2026-01", -1, "2025-12"), ("2025-02", -1, "2025-01"), ("2025-12", 1, "2026-01")],
)
def test_month_shift(month: str, shift: int, expected: str) -> None:
    from taxi_train_and_promote import month_shift

    assert month_shift(month, shift) == expected


def test_production_schedule_is_monthly(dagbag: DagBag) -> None:
    """Default (no TAXI_DAG_SCHEDULE) is the monthly cron; local compose overrides it."""
    assert dagbag.dags["taxi_monthly_refresh"].schedule == "0 6 15 * *"


def test_databricks_step_is_deferrable(dagbag: DagBag) -> None:
    task = dagbag.dags["taxi_monthly_refresh"].get_task("refresh_lakehouse")
    assert task.deferrable is True


def test_no_secrets_in_dag_source() -> None:
    source = "".join(p.read_text() for p in DAGS.rglob("*.py"))
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
