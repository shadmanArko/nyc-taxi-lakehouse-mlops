import json
from pathlib import Path
from typing import Any

import mlflow
import pytest

from taxi_portal.cli import publish
from taxi_portal.snapshot import (
    SECTIONS,
    build_snapshot,
    collect_analytics,
    collect_data_engineering,
    collect_devops,
    safe,
)


class FakeCursor:
    """Answers each query by matching a fragment of its SQL."""

    def __init__(self, answers: dict[str, list[tuple[Any, ...]]]) -> None:
        self.answers = answers
        self.queries: list[str] = []
        self._last: list[tuple[Any, ...]] = []

    def execute(self, operation: str, parameters: dict[str, Any] | None = None) -> None:
        self.queries.append(operation)
        self._last = next(v for k, v in self.answers.items() if k in operation)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._last


def test_data_engineering_funnel_per_month() -> None:
    cursor = FakeCursor(
        {
            ".taxi_bronze.yellow_trips": [("2025-01", 3475226), ("2025-02", 3577543)],
            ".taxi_silver.yellow_trips": [("2025-01", 3312645)],
            ".taxi_gold.trip_features": [("2025-01", 3287442)],
        }
    )
    rules = [{"rule": "duration_1_to_120_min", "passed": 10, "failed": 2}]
    out = collect_data_engineering(
        cursor, "workspace", "taxi_bronze", "taxi_silver", "taxi_gold", lambda: rules
    )
    assert out["funnel"] == [
        {"month": "2025-01", "bronze": 3475226, "silver": 3312645, "gold": 3287442},
        {"month": "2025-02", "bronze": 3577543},
    ]
    assert out["quality_rules"] == rules


def test_identifiers_are_validated() -> None:
    with pytest.raises(ValueError, match="identifier"):
        collect_data_engineering(FakeCursor({}), "workspace; DROP", "b", "s", "g")


def test_analytics_shapes_marts_for_charts() -> None:
    cursor = FakeCursor(
        {
            "max(source_month)": [("2025-01",)],
            "bi_hourly_demand": [(1, 8, 1000, 13.5, 6.1)],
            "bi_daily_trips ORDER": [("2025-01-01", 90000, 12.0, False)],
            "bi_pickup_zones": [(132, 50000, 38.2, 17.1, True)],
        }
    )
    out = collect_analytics(cursor, "workspace", "taxi_gold")
    assert out["latest_month"] == "2025-01"
    assert out["hourly"][0] == {
        "dow": 1,
        "hour": 8,
        "trips": 1000,
        "avg_duration_min": 13.5,
        "pace_min_per_mile": 6.1,
    }
    assert out["top_pickup_zones"][0]["airport"] is True


def test_devops_reads_public_workflow_runs() -> None:
    def fetch(url: str) -> Any:
        assert url.startswith("https://api.github.com/repos/o/r/actions/workflows/")
        return {
            "workflow_runs": [
                {
                    "conclusion": "success",
                    "head_sha": "a2d62e454e4f",
                    "created_at": "2026-11-23T10:00:00Z",
                    "html_url": "u",
                }
            ]
        }

    out = collect_devops("o/r", fetch)
    assert out["workflows"]["ci"][0] == {
        "status": "success",
        "commit": "a2d62e4",
        "when": "2026-11-23T10:00:00Z",
        "url": "u",
    }


def test_a_broken_source_marks_only_its_section() -> None:
    def boom() -> dict[str, Any]:
        raise ConnectionError("warehouse asleep")

    section = safe("analytics", boom)
    assert section["status"] == "unavailable"
    assert "warehouse asleep" in section["error"]


def test_snapshot_has_every_portal_section() -> None:
    snapshot = build_snapshot({"ml": {"status": "ok"}})
    for name in SECTIONS:
        assert name in snapshot
    assert snapshot["ml"] == {"status": "ok"}
    assert snapshot["devops"] == {"status": "unavailable"}
    assert snapshot["snapshot_version"] == 1


def test_publish_writes_portal_json_to_mlflow(tmp_path: Path) -> None:
    db = f"sqlite:///{tmp_path / 'mlflow.db'}"
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(db)
    try:
        run_id = publish(build_snapshot({}))
        path = mlflow.MlflowClient().download_artifacts(run_id, "portal.json", str(tmp_path))
        assert set(SECTIONS) <= set(json.loads(Path(path).read_text()))
    finally:
        mlflow.set_tracking_uri(previous)


def test_pipeline_expectations_read_the_latest_update_from_the_event_log() -> None:
    from taxi_portal.snapshot import pipeline_expectations

    payload = json.dumps(
        [
            {"name": "speed_at_most_80_mph", "passed_records": 990, "failed_records": 10},
            {"name": "location_ids_valid", "passed_records": 1000, "failed_records": 0},
        ]
    )
    cursor = FakeCursor({"event_log": [(payload,)]})
    rules = pipeline_expectations(cursor, "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
    assert rules[0] == {"rule": "speed_at_most_80_mph", "passed": 990, "failed": 10}
    assert (
        pipeline_expectations(FakeCursor({"event_log": []}), "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
        == []
    )
    with pytest.raises(ValueError, match="pipeline id"):
        pipeline_expectations(cursor, "x'); DROP TABLE t; --")
