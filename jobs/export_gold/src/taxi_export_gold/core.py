"""Query -> validate -> write -> publish. Publishing is idempotent."""

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from taxi_contracts import (
    CONTRACT_VERSION,
    GOLD_TARGET,
    GOLD_TRIP_FEATURES,
    GOLD_TRIP_FEATURES_TABLE,
)

logger = logging.getLogger("taxi_export_gold")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")
_MONTH = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")

# Contract SQL type -> predicate on the Arrow type the warehouse returns.
_ARROW_CHECKS: Final = {
    "string": lambda t: pa.types.is_string(t) or pa.types.is_large_string(t),
    "date": pa.types.is_date,
    "timestamp": pa.types.is_timestamp,
    "int": pa.types.is_integer,
    "double": pa.types.is_floating,
    "boolean": pa.types.is_boolean,
}


class ExportError(Exception):
    """The snapshot violates the gold contract and must not be published."""


def check_identifier(value: str, what: str) -> str:
    """Catalog/schema names are interpolated into SQL, so they must be plain identifiers."""
    if not _IDENTIFIER.match(value):
        msg = f"{what} must be a plain identifier, got {value!r}"
        raise ValueError(msg)
    return value


def check_month(value: str) -> str:
    if not _MONTH.match(value):
        msg = f"month must look like YYYY-MM, got {value!r}"
        raise ValueError(msg)
    return value


def build_query(catalog: str, schema: str) -> str:
    """Contract columns only, in contract order, one month (bound as a parameter)."""
    columns = ", ".join(GOLD_TRIP_FEATURES)
    table = (
        f"{check_identifier(catalog, 'catalog')}.{check_identifier(schema, 'schema')}."
        f"{GOLD_TRIP_FEATURES_TABLE}"
    )
    return (
        f"SELECT {columns} FROM {table} "  # noqa: S608 - identifiers validated above
        "WHERE source_month = CAST(:month_start AS DATE) ORDER BY trip_id"
    )


def validate(table: pa.Table) -> None:
    """Raise ExportError listing every way ``table`` breaks the gold contract."""
    problems: list[str] = []
    if table.column_names != list(GOLD_TRIP_FEATURES):
        problems.append(f"columns {table.column_names} != contract {list(GOLD_TRIP_FEATURES)}")
    else:
        for name, sql_type in GOLD_TRIP_FEATURES.items():
            arrow_type = table.schema.field(name).type
            if not _ARROW_CHECKS[sql_type](arrow_type):
                problems.append(f"{name} is {arrow_type}, expected {sql_type}")
        if table.column(GOLD_TARGET).null_count:
            problems.append(f"{GOLD_TARGET} has NULLs")
        if table.num_rows and len(set(table.column("trip_id").to_pylist())) != table.num_rows:
            problems.append("trip_id is not unique")
    if table.num_rows == 0:
        problems.append("no rows for this month")
    if problems:
        raise ExportError("gold snapshot violates the contract: " + "; ".join(problems))


def write_parquet(table: pa.Table, path: Path) -> str:
    """Write deterministically (sorted, fixed settings) and return the file's SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = table.sort_by("trip_id")
    pq.write_table(ordered, path, compression="zstd", write_statistics=True)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    dataset: str
    source_month: str
    sha256: str
    rows: int
    size_bytes: int
    columns: dict[str, str]
    contract_version: str
    source_table: str
    object_key: str
    created_at: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"


class ObjectStore(Protocol):
    """The slice of a boto3 S3 client we use."""

    def head_object(self, *, Bucket: str, Key: str) -> Any: ...  # noqa: N803 - boto3 API

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> Any: ...  # noqa: N803


def _exists(store: ObjectStore, bucket: str, key: str) -> bool:
    try:
        store.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _is_not_found(exc):
            return False
        raise
    return True


def _is_not_found(exc: Exception) -> bool:
    if isinstance(exc, KeyError):
        return True
    response = getattr(exc, "response", None)
    code = (response or {}).get("Error", {}).get("Code") if isinstance(response, dict) else None
    return code in {"404", "NoSuchKey", "NotFound"}


def publish(
    local_file: Path,
    sha256: str,
    rows: int,
    month: str,
    source_table: str,
    store: ObjectStore,
    bucket: str,
    prefix: str,
) -> SnapshotManifest:
    """Upload ``<prefix>/source_month=<m>/<sha>.parquet`` + manifest, then move LATEST.

    Objects are named by content, so re-publishing identical data is a no-op and a
    published snapshot can never change underneath a model trained on it.
    """
    base = f"{prefix.strip('/')}/source_month={month}"
    data_key = f"{base}/{sha256}.parquet"
    manifest = SnapshotManifest(
        dataset=GOLD_TRIP_FEATURES_TABLE,
        source_month=month,
        sha256=sha256,
        rows=rows,
        size_bytes=local_file.stat().st_size,
        columns=dict(GOLD_TRIP_FEATURES),
        contract_version=CONTRACT_VERSION,
        source_table=source_table,
        object_key=data_key,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    if _exists(store, bucket, data_key):
        logger.info("snapshot %s already published, not re-uploading", data_key)
    else:
        store.put_object(Bucket=bucket, Key=data_key, Body=local_file.read_bytes())
        store.put_object(
            Bucket=bucket,
            Key=f"{base}/{sha256}.manifest.json",
            Body=manifest.to_json().encode(),
            ContentType="application/json",
        )
        logger.info("published %s (%d rows)", data_key, rows)
    store.put_object(Bucket=bucket, Key=f"{base}/LATEST", Body=f"{sha256}\n".encode())
    return manifest


class Cursor(Protocol):
    def execute(self, operation: str, parameters: dict[str, Any] | None = None) -> Any: ...

    def fetchall_arrow(self) -> pa.Table: ...


def fetch_month(cursor: Cursor, catalog: str, schema: str, month: str) -> pa.Table:
    cursor.execute(build_query(catalog, schema), {"month_start": f"{check_month(month)}-01"})
    return cursor.fetchall_arrow()
