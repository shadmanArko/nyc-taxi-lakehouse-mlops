"""Command line: ``taxi-ingest --month 2025-01 [--upload --volume /Volumes/...]``.

Databricks credentials come from the standard SDK chain (a ``DATABRICKS_CONFIG_PROFILE``
profile in ~/.databrickscfg, or ``DATABRICKS_HOST`` + ``DATABRICKS_TOKEN``); nothing is
passed on the command line.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from taxi_ingest.core import IngestError, fetch, file_name, parse_month, upload

DEFAULT_DATA_DIR = Path("data/raw/tlc/yellow")
DEFAULT_VOLUME = "/Volumes/workspace/taxi_raw/landing/yellow"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taxi-ingest", description=__doc__)
    parser.add_argument("--month", required=True, type=parse_month, help="YYYY-MM")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--upload", action="store_true", help="also upload to the landing volume")
    parser.add_argument("--volume", default=DEFAULT_VOLUME, help="UC volume directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = fetch(args.month, args.data_dir)
        if args.upload:
            from databricks.sdk import WorkspaceClient

            upload(args.data_dir / file_name(args.month), args.volume, WorkspaceClient().files)
    except IngestError as exc:
        logging.getLogger("taxi_ingest").error("%s", exc)
        return 1
    print(f"{manifest.file} sha256={manifest.sha256} rows={manifest.rows}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
