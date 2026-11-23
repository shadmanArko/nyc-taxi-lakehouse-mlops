"""``taxi-train`` and ``taxi-promote``.

Data: ``--snapshot-path`` (local parquet) or, by default, the month's LATEST snapshot in
object storage (``TAXI_S3_ENDPOINT_URL`` + standard AWS credentials/profile).
Tracking: ``MLFLOW_TRACKING_URI`` (e.g. http://127.0.0.1:5050).
"""

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from taxi_train.data import Snapshot, load_from_store, load_local
from taxi_train.models import CANDIDATES


def _data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--month", required=True, help="YYYY-MM of the gold snapshot")
    parser.add_argument("--snapshot-path", type=Path, help="local parquet instead of the store")
    parser.add_argument("--snapshot-sha256", help="exact snapshot (default: the month's LATEST)")
    parser.add_argument("--bucket", default=os.environ.get("TAXI_SNAPSHOT_BUCKET", "taxi-dvc"))


def _load(args: argparse.Namespace) -> Snapshot:  # pragma: no cover - needs storage
    if args.snapshot_path:
        return load_local(args.snapshot_path, args.month)
    import boto3
    from botocore.config import Config

    store = boto3.client(
        "s3",
        endpoint_url=os.environ.get("TAXI_S3_ENDPOINT_URL"),
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={"addressing_style": "path"},
        ),
    )
    return load_from_store(store, args.bucket, args.month, sha256=args.snapshot_sha256)


def train_main(argv: Sequence[str] | None = None) -> int:
    from taxi_train.training import run

    parser = argparse.ArgumentParser(prog="taxi-train", description=__doc__)
    _data_args(parser)
    parser.add_argument("--model", choices=sorted(CANDIDATES), required=True)
    parser.add_argument("--tune-trials", type=int, default=0, help="Optuna trials (lightgbm)")
    parser.add_argument("--tune-sample", type=int, default=500_000)
    parser.add_argument("--train-sample", type=int, help="subsample training rows")
    parser.add_argument("--register", action="store_true", help="register as @challenger")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run(
        _load(args),
        args.model,
        tune_trials=args.tune_trials,
        tune_sample=args.tune_sample,
        train_sample=args.train_sample,
        register=args.register,
    )
    print(json.dumps({k: v for k, v in asdict(result).items() if k != "trials"}, indent=2))
    return 0


def promote_main(argv: Sequence[str] | None = None) -> int:
    from taxi_train.promote import evaluate_gate

    parser = argparse.ArgumentParser(prog="taxi-promote", description=__doc__)
    _data_args(parser)
    parser.add_argument("--min-improvement", type=float, default=0.005)
    parser.add_argument("--max-slice-regression", type=float, default=0.05)
    parser.add_argument("--dry-run", action="store_true", help="evaluate, do not move aliases")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    decision = evaluate_gate(
        _load(args),
        min_improvement=args.min_improvement,
        max_slice_regression=args.max_slice_regression,
        apply=not args.dry_run,
    )
    print(json.dumps(asdict(decision), indent=2))
    return 0 if decision.promoted else 3  # 3 = evaluated fine, but not promoted


if __name__ == "__main__":  # pragma: no cover
    sys.exit(train_main())


def _load_month(month: str, path: Path | None, bucket: str) -> Snapshot:  # pragma: no cover
    return _load(
        argparse.Namespace(month=month, snapshot_path=path, snapshot_sha256=None, bucket=bucket)
    )


def monitor_main(argv: Sequence[str] | None = None) -> int:
    """``taxi-monitor --reference-month 2025-01 --current-month 2025-02``."""
    from taxi_train.monitor_run import run_monitoring

    parser = argparse.ArgumentParser(prog="taxi-monitor", description="Drift report")
    parser.add_argument("--reference-month", required=True, help="training month (YYYY-MM)")
    parser.add_argument("--current-month", required=True, help="newer month (YYYY-MM)")
    parser.add_argument("--reference-path", type=Path, help="local parquet instead of the store")
    parser.add_argument("--current-path", type=Path, help="local parquet instead of the store")
    parser.add_argument("--bucket", default=os.environ.get("TAXI_SNAPSHOT_BUCKET", "taxi-dvc"))
    parser.add_argument("--no-model", action="store_true", help="data drift only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    reference = _load_month(args.reference_month, args.reference_path, args.bucket)
    current = _load_month(args.current_month, args.current_path, args.bucket)
    report = run_monitoring(reference, current, with_model=not args.no_model)
    print(json.dumps(report.to_dict(), indent=2))
    return 0
