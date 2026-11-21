"""``taxi-export-gold --month 2025-01 --schema taxi_gold``.

Credentials come only from the environment / standard config, never arguments:
* Databricks: SDK unified auth (``DATABRICKS_HOST`` + ``DATABRICKS_TOKEN``, or a
  ``DATABRICKS_CONFIG_PROFILE``) plus ``DATABRICKS_WAREHOUSE_ID``.
* Object storage: standard AWS variables or profile, plus ``TAXI_S3_ENDPOINT_URL``.
"""

import argparse
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from taxi_export_gold.core import (
    ExportError,
    ObjectStore,
    check_identifier,
    check_month,
    fetch_month,
    publish,
    validate,
    write_parquet,
)

log = logging.getLogger("taxi_export_gold")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taxi-export-gold", description=__doc__)
    parser.add_argument("--month", required=True, type=check_month, help="YYYY-MM")
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--schema", required=True, help="gold schema, e.g. taxi_gold")
    parser.add_argument("--bucket", default=os.environ.get("TAXI_SNAPSHOT_BUCKET", "taxi-dvc"))
    parser.add_argument("--prefix", default="snapshots/gold/trip_features")
    return parser


def _open_cursor() -> Any:  # pragma: no cover - needs a live warehouse
    from databricks.sdk.core import Config

    from databricks import sql

    config = Config()
    warehouse = os.environ["DATABRICKS_WAREHOUSE_ID"]
    connection = sql.connect(
        server_hostname=config.host.removeprefix("https://").rstrip("/"),
        http_path=f"/sql/1.0/warehouses/{warehouse}",
        credentials_provider=lambda: config.authenticate,
    )
    return connection.cursor()


def _object_store() -> ObjectStore:  # pragma: no cover - needs object storage
    import boto3
    from botocore.config import Config as BotoConfig

    client: ObjectStore = boto3.client(
        "s3",
        endpoint_url=os.environ.get("TAXI_S3_ENDPOINT_URL"),
        config=BotoConfig(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={"addressing_style": "path"},
        ),
    )
    return client


def main(
    argv: Sequence[str] | None = None, cursor: Any = None, store: ObjectStore | None = None
) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    check_identifier(args.catalog, "catalog")
    check_identifier(args.schema, "schema")
    source = f"{args.catalog}.{args.schema}.trip_features"
    try:
        table = fetch_month(cursor or _open_cursor(), args.catalog, args.schema, args.month)
        validate(table)
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / f"trip_features_{args.month}.parquet"
            sha = write_parquet(table, local)
            manifest = publish(
                local,
                sha,
                table.num_rows,
                args.month,
                source,
                store or _object_store(),
                args.bucket,
                args.prefix,
            )
    except ExportError as exc:
        log.error("%s", exc)
        return 1
    print(f"s3://{args.bucket}/{manifest.object_key} rows={manifest.rows}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
