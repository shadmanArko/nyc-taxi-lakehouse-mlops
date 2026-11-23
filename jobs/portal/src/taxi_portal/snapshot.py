"""Collectors: each department's numbers from its own system of record.

Every collector is independent and defensive: if a source is down, that section is
marked ``unavailable`` and the snapshot is still published with everything else.
"""

import json
import logging
import re
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final, Protocol

log = logging.getLogger("taxi_portal")

SNAPSHOT_VERSION: Final = 1
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MODEL_NAME: Final = "taxi-trip-duration"
SECTIONS: Final = ("data_engineering", "analytics", "ml", "mlops", "monitoring", "devops")


class Cursor(Protocol):
    def execute(self, operation: str, parameters: dict[str, Any] | None = None) -> Any: ...

    def fetchall(self) -> list[Any]: ...


def _ident(value: str) -> str:
    if not _IDENT.match(value):
        msg = f"not a plain identifier: {value!r}"
        raise ValueError(msg)
    return value


def _rows(cursor: Cursor, sql: str) -> list[tuple[Any, ...]]:
    cursor.execute(sql)
    return [tuple(r) for r in cursor.fetchall()]


def _num(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return round(float(value), 4)


# --- Data engineering -----------------------------------------------------------


def collect_data_engineering(
    cursor: Cursor,
    catalog: str,
    bronze: str,
    silver: str,
    gold: str,
    expectations: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    cat, b, s, g = (_ident(x) for x in (catalog, bronze, silver, gold))
    month_of_file = "regexp_extract(_source_file, '([0-9]{4}-[0-9]{2})[.]parquet$', 1)"
    layers = {
        "bronze": f"SELECT {month_of_file} AS m, count(*) FROM {cat}.{b}.yellow_trips GROUP BY 1",
        "silver": f"SELECT date_format(source_month, 'yyyy-MM') AS m, count(*) FROM {cat}.{s}.yellow_trips GROUP BY 1",  # noqa: E501
        "gold": f"SELECT date_format(source_month, 'yyyy-MM') AS m, count(*) FROM {cat}.{g}.trip_features GROUP BY 1",  # noqa: E501
    }
    months: dict[str, dict[str, int]] = {}
    for layer, sql in layers.items():
        for month, count in _rows(cursor, sql):
            months.setdefault(str(month), {})[layer] = int(count)
    funnel = [{"month": m, **months[m]} for m in sorted(months)]
    return {
        "status": "ok",
        "funnel": funnel,
        "quality_rules": expectations() if expectations else [],
        "tools": [
            "Databricks (Unity Catalog, Delta)",
            "Lakeflow Declarative Pipelines",
            "Auto Loader",
            "dbt",
            "DVC",
            "SeaweedFS (S3)",
        ],
    }


_PIPELINE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def pipeline_expectations(cursor: Cursor, pipeline_id: str) -> list[dict[str, Any]]:
    """Per-rule pass/fail counts from the latest completed Lakeflow update.

    Read from the pipeline's event log (``event_log()``): the REST event listing omits
    the ``details`` payload that carries the data-quality numbers.
    """
    if not _PIPELINE_ID.match(pipeline_id):
        msg = f"not a pipeline id: {pipeline_id!r}"
        raise ValueError(msg)
    rows = _rows(
        cursor,
        f"""SELECT details:flow_progress.data_quality.expectations
        FROM event_log('{pipeline_id}')
        WHERE event_type = 'flow_progress'
          AND details:flow_progress.data_quality.expectations IS NOT NULL
        ORDER BY timestamp DESC LIMIT 1""",
    )
    if not rows:
        return []
    return [
        {
            "rule": e["name"],
            "passed": int(e.get("passed_records", 0)),
            "failed": int(e.get("failed_records", 0)),
        }
        for e in json.loads(rows[0][0])
    ]


def pipeline_id_by_name(workspace: Any, name: str) -> str:
    return str(next(p for p in workspace.pipelines.list_pipelines() if p.name == name).pipeline_id)


# --- Analytics / BI -------------------------------------------------------------


def collect_analytics(cursor: Cursor, catalog: str, gold: str) -> dict[str, Any]:
    cat, g = _ident(catalog), _ident(gold)
    latest = _rows(
        cursor, f"SELECT date_format(max(source_month), 'yyyy-MM') FROM {cat}.{g}.bi_daily_trips"
    )[0][0]
    if not re.fullmatch(r"20\d{2}-\d{2}", str(latest)):
        msg = f"unexpected latest month {latest!r}"
        raise ValueError(msg)
    hourly = _rows(
        cursor,
        f"""SELECT pickup_day_of_week, pickup_hour, sum(trips),
               sum(trips * avg_duration_min) / sum(trips),
               sum(trips * avg_duration_min) / nullif(sum(trips * avg_distance_mi), 0)
        FROM {cat}.{g}.bi_hourly_demand WHERE date_format(source_month, 'yyyy-MM') = '{latest}'
        GROUP BY 1, 2 ORDER BY 1, 2""",
    )
    daily = _rows(
        cursor,
        f"""SELECT cast(pickup_date AS string), trips, avg_duration_min, is_weekend
        FROM {cat}.{g}.bi_daily_trips ORDER BY pickup_date""",
    )
    zones = _rows(
        cursor,
        f"""SELECT pickup_location_id, trips, avg_duration_min, avg_distance_mi, is_airport
        FROM {cat}.{g}.bi_pickup_zones WHERE date_format(source_month, 'yyyy-MM') = '{latest}'
        ORDER BY trips DESC LIMIT 15""",
    )
    return {
        "status": "ok",
        "latest_month": latest,
        "hourly": [
            {
                "dow": int(d),
                "hour": int(h),
                "trips": int(t),
                "avg_duration_min": _num(a),
                "pace_min_per_mile": _num(p),
            }
            for d, h, t, a, p in hourly
        ],
        "daily": [
            {"date": d, "trips": int(t), "avg_duration_min": _num(a), "weekend": bool(w)}
            for d, t, a, w in daily
        ],
        "top_pickup_zones": [
            {
                "zone_id": int(z),
                "trips": int(t),
                "avg_duration_min": _num(a),
                "avg_distance_mi": _num(m),
                "airport": bool(ap),
            }
            for z, t, a, m, ap in zones
        ],
        "tools": ["dbt marts on gold", "Databricks SQL warehouse"],
    }


# --- ML + MLOps (MLflow) --------------------------------------------------------


def collect_ml(client: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    import mlflow

    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    registered = client.get_registered_model(MODEL_NAME)
    aliases = {alias: str(v) for alias, v in (registered.aliases or {}).items()}
    by_version = {
        str(v): sorted(a for a, vv in aliases.items() if vv == str(v))
        for v in [x.version for x in versions]
    }
    rows = []
    for v in sorted(versions, key=lambda x: int(x.version)):
        run = mlflow.get_run(v.run_id) if v.run_id else None
        metrics = run.data.metrics if run else {}
        tags = run.data.tags if run else {}
        rows.append(
            {
                "version": str(v.version),
                "aliases": by_version.get(str(v.version), []),
                "model_family": tags.get("model_family"),
                "test_rmse": _num(metrics.get("test_rmse")),
                "test_mae": _num(metrics.get("test_mae")),
                "test_r2": _num(metrics.get("test_r2")),
                "val_rmse": _num(metrics.get("val_rmse")),
                "gate_result": v.tags.get("gate_result"),
                "snapshot_sha256": v.tags.get("snapshot_sha256"),
                "trained_on": tags.get("source_month"),
                "slices": {
                    k.removeprefix("test_rmse__"): _num(val)
                    for k, val in metrics.items()
                    if k.startswith("test_rmse__")
                },
            }
        )
    champion = next((r for r in rows if "champion" in r["aliases"]), None)
    baseline = mlflow.search_runs(
        experiment_names=[MODEL_NAME],
        filter_string="tags.model_family = 'mean_baseline'",
        max_results=1,
        output_format="list",
    )
    ml = {
        "status": "ok",
        "champion": champion,
        "candidates": rows,
        "baseline_test_rmse": _num(baseline[0].data.metrics.get("test_rmse")) if baseline else None,
        "features": [
            "pickup zone",
            "dropoff zone",
            "zone pair (route)",
            "trip distance",
            "log distance",
            "pickup hour (+ cyclic)",
            "ISO weekday",
            "weekend flag",
        ],
        "tools": ["scikit-learn", "LightGBM", "Optuna", "MLflow"],
    }
    mlops = {
        "status": "ok",
        "registry": [
            {
                "version": r["version"],
                "aliases": r["aliases"],
                "gate_result": r["gate_result"],
                "test_rmse": r["test_rmse"],
                "model_family": r["model_family"],
            }
            for r in rows
        ],
        "aliases": aliases,
        "gate_rules": [
            "beats mean baseline",
            "RMSE >= 0.5% better than champion on the same test week",
            "no slice more than 5% worse",
            "old champion kept as @previous",
        ],
        "schedule": (
            "15th of each month, 06:00 UTC: ingest → lakehouse → snapshot → train → gate → monitor"
        ),
        "tools": [
            "MLflow registry + aliases",
            "Airflow 3 (asset-triggered)",
            "Sablier scale-to-zero",
        ],
    }
    return ml, mlops


def collect_monitoring(client: Any) -> dict[str, Any]:
    import mlflow

    runs = mlflow.search_runs(
        experiment_names=["taxi-monitoring"],
        order_by=["attributes.start_time DESC"],
        max_results=1,
        output_format="list",
    )
    if not runs:
        return {"status": "empty"}
    path = client.download_artifacts(runs[0].info.run_id, "drift_report.json")
    with open(path, encoding="utf-8") as fh:  # noqa: PTH123 - path is a str from MLflow
        report = json.load(fh)
    return {"status": "ok", "report": report, "tools": ["PSI drift", "champion RMSE on new month"]}


# --- DevOps (GitHub Actions, public API) ----------------------------------------


def collect_devops(repo: str, fetch: Callable[[str], Any] | None = None) -> dict[str, Any]:
    def default_fetch(url: str) -> Any:
        if not url.startswith("https://api.github.com/"):
            msg = "unexpected URL"
            raise ValueError(msg)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "taxi-portal",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    get = fetch or default_fetch
    workflows = {}
    for name in ("ci.yml", "cd.yml"):
        data = get(
            f"https://api.github.com/repos/{repo}/actions/workflows/{name}/runs?branch=main&per_page=5"
        )
        workflows[name.removesuffix(".yml")] = [
            {
                "status": r.get("conclusion") or r.get("status"),
                "commit": r["head_sha"][:7],
                "when": r.get("created_at"),
                "url": r.get("html_url"),
            }
            for r in data.get("workflow_runs", [])
        ]
    return {
        "status": "ok",
        "workflows": workflows,
        "pipeline": [
            "pre-commit (format, lint, secrets)",
            "commit-msg (Conventional Commits)",
            "pre-push (mypy, tests)",
            "CI (tests, dbt, DAGs, 5 image builds + scans)",
            "CD (signed images to GHCR)",
            "operator-approved rollout",
        ],
        "versioning": [
            "Git (code)",
            "uv.lock (dependencies)",
            "DVC (raw data)",
            "SHA-256 snapshots (gold)",
            "MLflow registry (models)",
            "image digests",
        ],
        "tools": ["GitHub Actions", "pre-commit", "Grype", "GHCR", "Dependabot", "gitleaks"],
    }


# --- Assembly -------------------------------------------------------------------


def safe(name: str, collect: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return collect()
    except Exception as exc:
        log.warning("portal section %s unavailable: %s", name, exc)
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"[:300]}


def build_snapshot(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **{name: sections.get(name, {"status": "unavailable"}) for name in SECTIONS},
    }
