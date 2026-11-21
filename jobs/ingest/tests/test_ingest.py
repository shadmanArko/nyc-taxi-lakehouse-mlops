import io
from datetime import datetime
from pathlib import Path
from typing import IO, BinaryIO

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from taxi_contracts import RAW_YELLOW_REQUIRED
from taxi_ingest import cli
from taxi_ingest.core import (
    IngestError,
    Manifest,
    fetch,
    file_name,
    manifest_path,
    parse_month,
    source_url,
    upload,
    verify,
)

_TYPES = {
    "integer": pa.int64(),
    "float": pa.float64(),
    "timestamp": pa.timestamp("us"),
    "string": pa.large_string(),
}


def make_parquet(
    drop: str | None = None, retype: tuple[str, pa.DataType] | None = None, rows: int = 3
) -> bytes:
    columns: dict[str, pa.Array] = {}
    for name, family in RAW_YELLOW_REQUIRED.items():
        if name == drop:
            continue
        arrow_type = retype[1] if retype and retype[0] == name else _TYPES[family.value]
        if pa.types.is_timestamp(arrow_type):
            values: list[object] = [datetime(2025, 1, 1, 8, i) for i in range(rows)]  # noqa: DTZ001
        elif pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
            values = ["N"] * rows
        else:
            values = [1] * rows
        columns[name] = pa.array(values, type=arrow_type)
    columns["cbd_congestion_fee"] = pa.array([0.75] * rows)  # an extra, newer column
    buffer = io.BytesIO()
    pq.write_table(pa.table(columns), buffer)
    return buffer.getvalue()


class FakeOpener:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def __call__(self, url: str) -> IO[bytes]:
        self.urls.append(url)
        return io.BytesIO(self.payload)


class FakeFiles:
    def __init__(self) -> None:
        self.uploaded: dict[str, bytes] = {}

    def upload(self, file_path: str, contents: BinaryIO, *, overwrite: bool | None = None) -> None:
        assert overwrite is True
        self.uploaded[file_path] = contents.read()


# --- naming ---------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["2025-13", "2025-1", "25-01", "2025/01", "1999-01", ""])
def test_parse_month_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        parse_month(bad)


def test_source_url_and_file_name() -> None:
    assert file_name("2025-01") == "yellow_tripdata_2025-01.parquet"
    assert source_url("2025-01", "https://x/") == "https://x/yellow_tripdata_2025-01.parquet"


# --- fetch + verify -------------------------------------------------------------


def test_fetch_downloads_verifies_and_records(tmp_path: Path) -> None:
    opener = FakeOpener(make_parquet())
    manifest = fetch("2025-01", tmp_path, opener=opener)
    assert manifest.rows == 3
    assert manifest.extra_columns == ["cbd_congestion_fee"]
    assert len(manifest.sha256) == 64
    assert Manifest.read(manifest_path(tmp_path / file_name("2025-01"))) == manifest
    assert not list(tmp_path.glob("*.part"))  # atomic write leaves no partial file


def test_fetch_is_idempotent(tmp_path: Path) -> None:
    opener = FakeOpener(make_parquet())
    first = fetch("2025-01", tmp_path, opener=opener)
    second = fetch("2025-01", tmp_path, opener=opener)
    assert first == second
    assert len(opener.urls) == 1  # second call did not download again


def test_fetch_reverifies_a_tampered_file(tmp_path: Path) -> None:
    opener = FakeOpener(make_parquet())
    fetch("2025-01", tmp_path, opener=opener)
    (tmp_path / file_name("2025-01")).write_bytes(make_parquet(rows=5))
    assert fetch("2025-01", tmp_path, opener=opener).rows == 5


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (make_parquet(drop="trip_distance"), "missing column trip_distance"),
        (make_parquet(retype=("PULocationID", pa.string())), "PULocationID is string"),
        (make_parquet(rows=0), "no rows"),
        (b"<html>Access denied</html>", "not a readable parquet"),
    ],
)
def test_contract_violations_never_land(tmp_path: Path, payload: bytes, message: str) -> None:
    with pytest.raises(IngestError, match=message):
        fetch("2025-01", tmp_path, opener=FakeOpener(payload))
    assert not manifest_path(tmp_path / file_name("2025-01")).exists()


def test_int32_and_int64_are_both_accepted(tmp_path: Path) -> None:
    """The TLC has switched integer widths between months; the family is what matters."""
    path = tmp_path / file_name("2025-01")
    path.write_bytes(make_parquet(retype=("VendorID", pa.int32())))
    assert verify(path, "https://x").rows == 3


def test_non_https_sources_are_refused(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="non-HTTPS"):
        fetch("2025-01", tmp_path, base_url="http://example.com/trip-data")


# --- upload ---------------------------------------------------------------------


def test_upload_puts_data_in_landing_and_manifest_aside(tmp_path: Path) -> None:
    fetch("2025-01", tmp_path, opener=FakeOpener(make_parquet()))
    files = FakeFiles()
    target = upload(tmp_path / file_name("2025-01"), "/Volumes/c/s/v/yellow/", files)
    assert target == "/Volumes/c/s/v/yellow/yellow_tripdata_2025-01.parquet"
    assert set(files.uploaded) == {
        target,
        "/Volumes/c/s/v/yellow/_manifests/yellow_tripdata_2025-01.parquet.manifest.json",
    }


def test_upload_requires_a_volume_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unity Catalog volume"):
        upload(tmp_path / "x.parquet", "/tmp/landing", FakeFiles())  # noqa: S108


def test_upload_requires_a_verified_file(tmp_path: Path) -> None:
    (tmp_path / "x.parquet").write_bytes(b"")
    with pytest.raises(IngestError, match="no manifest"):
        upload(tmp_path / "x.parquet", "/Volumes/c/s/v", FakeFiles())


# --- cli ------------------------------------------------------------------------


def test_cli_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("taxi_ingest.core._https_open", FakeOpener(make_parquet()))
    monkeypatch.setattr(
        "taxi_ingest.cli.fetch",
        lambda month, data_dir: fetch(month, data_dir, opener=FakeOpener(make_parquet())),
    )
    assert cli.main(["--month", "2025-01", "--data-dir", str(tmp_path)]) == 0
    assert "rows=3" in capsys.readouterr().out


def test_cli_returns_1_on_contract_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "taxi_ingest.cli.fetch",
        lambda month, data_dir: fetch(month, data_dir, opener=FakeOpener(b"junk")),
    )
    assert cli.main(["--month", "2025-01", "--data-dir", str(tmp_path)]) == 1


def test_cli_rejects_bad_month() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--month", "January"])
