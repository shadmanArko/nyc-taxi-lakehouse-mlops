"""Download -> verify -> record -> upload. Each step is idempotent."""

import hashlib
import json
import logging
import re
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, BinaryIO, Final, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from taxi_contracts import RAW_YELLOW_REQUIRED, TypeFamily

logger = logging.getLogger("taxi_ingest")

TLC_BASE_URL: Final = "https://d37ci6vzurychx.cloudfront.net/trip-data"
_MONTH = re.compile(r"^(?P<year>20\d{2})-(?P<month>0[1-9]|1[0-2])$")
_CHUNK = 1 << 20


class IngestError(Exception):
    """A file failed verification and must not land in the lakehouse."""


def parse_month(value: str) -> str:
    """Validate a ``YYYY-MM`` month string."""
    if not _MONTH.match(value):
        msg = f"month must look like YYYY-MM, got {value!r}"
        raise ValueError(msg)
    return value


def file_name(month: str) -> str:
    return f"yellow_tripdata_{parse_month(month)}.parquet"


def source_url(month: str, base_url: str = TLC_BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/{file_name(month)}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


Opener = Callable[[str], IO[bytes]]


def _https_open(url: str) -> IO[bytes]:
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        msg = f"refusing non-HTTPS source: {url}"
        raise IngestError(msg)
    request = urllib.request.Request(url, headers={"User-Agent": "taxi-ingest/0.1"})  # noqa: S310
    response: IO[bytes] = urllib.request.urlopen(request, timeout=60)  # noqa: S310 - scheme checked
    return response


def download(url: str, dest: Path, opener: Opener = _https_open) -> Path:
    """Stream ``url`` to ``dest`` atomically (``.part`` file, then rename)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    with opener(url) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, _CHUNK)
    partial.replace(dest)
    return dest


def _family(arrow_type: pa.DataType) -> TypeFamily | None:
    if pa.types.is_integer(arrow_type):
        return TypeFamily.INTEGER
    if pa.types.is_floating(arrow_type):
        return TypeFamily.FLOAT
    if pa.types.is_timestamp(arrow_type):
        return TypeFamily.TIMESTAMP
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return TypeFamily.STRING
    return None


@dataclass(frozen=True, slots=True)
class Manifest:
    """What we know about a landed file; written next to it as JSON."""

    file: str
    source_url: str
    sha256: str
    size_bytes: int
    rows: int
    columns: list[str]
    extra_columns: list[str]
    verified_at: str

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        return cls(**json.loads(path.read_text()))


def manifest_path(data_file: Path) -> Path:
    return data_file.with_name(data_file.name + ".manifest.json")


def verify(path: Path, url: str) -> Manifest:
    """Check the file against the raw contract. Raises IngestError listing every problem."""
    try:
        parquet = pq.ParquetFile(path)
    except (pa.ArrowInvalid, OSError) as exc:
        msg = f"{path.name} is not a readable parquet file: {exc}"
        raise IngestError(msg) from exc

    schema = parquet.schema_arrow
    present = {field.name: field.type for field in schema}
    problems: list[str] = []
    for name, family in RAW_YELLOW_REQUIRED.items():
        if name not in present:
            problems.append(f"missing column {name}")
        elif _family(present[name]) is not family:
            problems.append(f"column {name} is {present[name]}, expected {family.value}")
    rows = parquet.metadata.num_rows
    if rows == 0:
        problems.append("file has no rows")
    if problems:
        raise IngestError(f"{path.name} violates the raw contract: " + "; ".join(problems))

    extra = sorted(set(present) - set(RAW_YELLOW_REQUIRED))
    return Manifest(
        file=path.name,
        source_url=url,
        sha256=sha256_of(path),
        size_bytes=path.stat().st_size,
        rows=rows,
        columns=list(present),
        extra_columns=extra,
        verified_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def fetch(
    month: str, data_dir: Path, opener: Opener = _https_open, base_url: str = TLC_BASE_URL
) -> Manifest:
    """Ensure a verified copy of ``month`` exists in ``data_dir``. Skips work already done."""
    url = source_url(month, base_url)
    dest = data_dir / file_name(month)
    record = manifest_path(dest)
    if dest.exists() and record.exists():
        known = Manifest.read(record)
        if known.sha256 == sha256_of(dest):
            logger.info(
                "%s already verified (sha256 %s...), skipping download",
                dest.name,
                known.sha256[:12],
            )
            return known
        logger.warning("%s changed since it was verified; re-downloading", dest.name)
    if not dest.exists():
        logger.info("downloading %s", url)
        download(url, dest, opener)
    manifest = verify(dest, url)
    manifest.write(record)
    logger.info("verified %s: %d rows, %d bytes", dest.name, manifest.rows, manifest.size_bytes)
    return manifest


class FilesClient(Protocol):
    """The slice of ``databricks.sdk.WorkspaceClient().files`` we use."""

    def upload(
        self, file_path: str, contents: BinaryIO, *, overwrite: bool | None = None
    ) -> object: ...


def upload(data_file: Path, volume_dir: str, files: FilesClient) -> str:
    """Upload a verified file and its manifest into a UC volume directory."""
    if not volume_dir.startswith("/Volumes/"):
        msg = f"volume_dir must be a Unity Catalog volume path, got {volume_dir!r}"
        raise ValueError(msg)
    record = manifest_path(data_file)
    if not record.exists():
        msg = f"{data_file.name} has no manifest; run fetch first"
        raise IngestError(msg)
    target = f"{volume_dir.rstrip('/')}/{data_file.name}"
    with data_file.open("rb") as fh:
        files.upload(target, fh, overwrite=True)
    # The manifest lands outside the Auto Loader path so it is never read as trip data.
    with record.open("rb") as fh:
        files.upload(f"{volume_dir.rstrip('/')}/_manifests/{record.name}", fh, overwrite=True)
    logger.info("uploaded %s to %s", data_file.name, target)
    return target
