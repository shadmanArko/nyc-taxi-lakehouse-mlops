"""Data assets shared between DAGs (Airflow data-aware scheduling).

The refresh DAG emits an event on GOLD_SNAPSHOTS after publishing a snapshot, with
``{"month": ..., }`` in the event extra; the training DAG is scheduled on that asset.
"""

from airflow.sdk import Asset

GOLD_SNAPSHOTS = Asset(
    name="taxi_gold_trip_features_snapshots",
    uri="s3://taxi-dvc/snapshots/gold/trip_features",
    group="gold",
)
