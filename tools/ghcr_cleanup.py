"""Delete old container image versions from GHCR, keeping the newest N tagged images.

    GH_TOKEN=... python3 tools/ghcr_cleanup.py --owner OWNER --package taxi-api --keep 10

Why not a generic "keep N versions" action: every image CD pushes is an index plus
*untagged* child manifests (the platform image and its provenance attestation). Counting
those as versions would delete children of images we mean to keep and break them. Here
only tagged versions are counted; a version is deleted only if it is older than the
oldest kept tagged image (minus a margin that covers its children). ``main`` is never
deleted. Standard library only, so it runs on a bare CI runner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

API = "https://api.github.com"
PROTECTED_TAGS = frozenset({"main"})


@dataclass(frozen=True)
class Version:
    id: int
    created: datetime
    tags: tuple[str, ...]


def parse(raw: dict[str, Any]) -> Version:
    tags = raw.get("metadata", {}).get("container", {}).get("tags", [])
    return Version(
        id=int(raw["id"]),
        created=datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00")),
        tags=tuple(tags),
    )


def select_for_deletion(
    versions: Iterable[Version], keep: int, margin: timedelta = timedelta(hours=1)
) -> list[Version]:
    """Versions to delete: everything older than the ``keep``-th newest tagged image."""
    if keep < 1:
        msg = "keep must be at least 1"
        raise ValueError(msg)
    every = list(versions)
    tagged = sorted((v for v in every if v.tags), key=lambda v: v.created, reverse=True)
    if len(tagged) <= keep:
        return []
    kept = {v.id for v in tagged[:keep]}
    cutoff = tagged[keep - 1].created - margin
    return sorted(
        (
            v
            for v in every
            if v.id not in kept and v.created < cutoff and not PROTECTED_TAGS & set(v.tags)
        ),
        key=lambda v: v.created,
    )


def _request(method: str, url: str, token: str) -> Any:
    if not url.startswith(API + "/"):
        msg = f"unexpected URL {url}"
        raise ValueError(msg)
    request = urllib.request.Request(  # noqa: S310 - fixed https://api.github.com prefix
        url,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ghcr-cleanup",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - checked above
        body = response.read()
        return json.loads(body) if body else None


def list_versions(base: str, call: Callable[[str, str], Any]) -> list[Version]:
    versions: list[Version] = []
    page = 1
    while True:
        batch = call("GET", f"{base}/versions?per_page=100&page={page}")
        if not batch:
            return versions
        versions.extend(parse(v) for v in batch)
        page += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner", required=True, help="user that owns the packages")
    parser.add_argument("--package", required=True, help="container package name")
    parser.add_argument("--keep", type=int, default=10, help="tagged images to keep")
    parser.add_argument("--dry-run", action="store_true", help="list, do not delete")
    args = parser.parse_args(argv)
    token = os.environ["GH_TOKEN"]

    def call(method: str, url: str) -> Any:
        return _request(method, url, token)

    base = f"{API}/users/{args.owner}/packages/container/{args.package}"
    versions = list_versions(base, call)
    doomed = select_for_deletion(versions, args.keep)
    print(f"{args.package}: {len(versions)} versions, deleting {len(doomed)} (keep {args.keep})")
    for v in doomed:
        label = ",".join(v.tags) or "untagged"
        print(f"  {'would delete' if args.dry_run else 'delete'} {v.id} {v.created:%F} {label}")
        if not args.dry_run:
            call("DELETE", f"{base}/versions/{v.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
