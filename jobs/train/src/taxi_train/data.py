"""Load a gold snapshot by content hash, and split it by time."""

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

import pandas as pd
import pyarrow.parquet as pq

from taxi_contracts import GOLD_TARGET, GOLD_TRIP_FEATURES

DEFAULT_PREFIX: Final = "snapshots/gold/trip_features"


class SnapshotError(Exception):
    """The snapshot is missing, corrupted or does not match the gold contract."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    frame: pd.DataFrame
    sha256: str
    source_month: str
    uri: str


class ObjectReader(Protocol):
    def get_object(self, *, Bucket: str, Key: str) -> Any: ...  # noqa: N803 - boto3 API


def _check(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(GOLD_TRIP_FEATURES) - set(frame.columns)
    if missing:
        msg = f"snapshot is missing gold columns: {sorted(missing)}"
        raise SnapshotError(msg)
    if frame.empty:
        msg = "snapshot has no rows"
        raise SnapshotError(msg)
    if frame[GOLD_TARGET].isna().any():
        msg = f"snapshot has NULL {GOLD_TARGET}"
        raise SnapshotError(msg)
    return frame


def _read(payload: bytes) -> pd.DataFrame:
    return pq.read_table(io.BytesIO(payload)).to_pandas()


def load_local(path: Path, month: str) -> Snapshot:
    payload = path.read_bytes()
    return Snapshot(
        frame=_check(_read(payload)),
        sha256=hashlib.sha256(payload).hexdigest(),
        source_month=month,
        uri=path.resolve().as_uri(),
    )


def load_from_store(
    store: ObjectReader,
    bucket: str,
    month: str,
    prefix: str = DEFAULT_PREFIX,
    sha256: str | None = None,
) -> Snapshot:
    """Fetch ``sha256`` (default: the month's LATEST) and verify the bytes match the hash."""
    base = f"{prefix.strip('/')}/source_month={month}"
    if sha256 is None:
        sha256 = (
            store.get_object(Bucket=bucket, Key=f"{base}/LATEST")["Body"].read().decode().strip()
        )
    key = f"{base}/{sha256}.parquet"
    payload = store.get_object(Bucket=bucket, Key=key)["Body"].read()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != sha256:
        msg = f"snapshot integrity check failed for {key}: content hash is {actual}"
        raise SnapshotError(msg)
    return Snapshot(
        frame=_check(_read(payload)), sha256=sha256, source_month=month, uri=f"s3://{bucket}/{key}"
    )


@dataclass(frozen=True, slots=True)
class Split:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame

    def ranges(self) -> dict[str, str]:
        def span(frame: pd.DataFrame) -> str:
            return f"{frame['pickup_date'].min()}..{frame['pickup_date'].max()}"

        return {
            "train": span(self.train),
            "validation": span(self.validation),
            "test": span(self.test),
        }


def temporal_split(frame: pd.DataFrame, validation_days: int = 7, test_days: int = 7) -> Split:
    """Last ``test_days`` calendar days = test, the ``validation_days`` before = validation.

    Splitting by time (not at random) means no day leaks across sets, and the test set
    looks like what production sees: days the model has never trained on.
    """
    dates = sorted(pd.to_datetime(frame["pickup_date"]).dt.date.unique())
    if len(dates) < validation_days + test_days + 1:
        msg = f"need more than {validation_days + test_days} days of data, got {len(dates)}"
        raise SnapshotError(msg)
    test_start = dates[-test_days]
    validation_start = dates[-(test_days + validation_days)]
    day = pd.to_datetime(frame["pickup_date"]).dt.date
    return Split(
        train=frame[day < validation_start].reset_index(drop=True),
        validation=frame[(day >= validation_start) & (day < test_start)].reset_index(drop=True),
        test=frame[day >= test_start].reset_index(drop=True),
    )
