"""Image retention: only old images go; kept images keep their untagged children."""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("ghcr_cleanup", ROOT / "tools/ghcr_cleanup.py")
assert _spec is not None
assert _spec.loader is not None
gc: Any = importlib.util.module_from_spec(_spec)
sys.modules["ghcr_cleanup"] = gc  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(gc)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def release(day: int, first_id: int, *tags: str) -> list[Any]:
    """One CD push: a tagged index + two untagged children a few seconds earlier."""
    at = T0 + timedelta(days=day)
    return [
        gc.Version(first_id, at - timedelta(seconds=4), ()),
        gc.Version(first_id + 1, at - timedelta(seconds=2), ()),
        gc.Version(first_id + 2, at, tags or (f"sha{day}",)),
    ]


def test_keeps_newest_tagged_images_with_their_children() -> None:
    versions = [v for day in range(12) for v in release(day, day * 10)]
    doomed = gc.select_for_deletion(versions, keep=10)
    # days 0 and 1 go entirely (index + children); days 2..11 stay intact
    assert {v.id for v in doomed} == {0, 1, 2, 10, 11, 12}


def test_nothing_to_delete_when_at_or_under_the_limit() -> None:
    versions = [v for day in range(10) for v in release(day, day * 10)]
    assert gc.select_for_deletion(versions, keep=10) == []


def test_main_tag_is_never_deleted() -> None:
    versions = release(0, 0, "main") + [v for day in range(1, 4) for v in release(day, day * 10)]
    doomed = gc.select_for_deletion(versions, keep=2)
    assert 2 not in {v.id for v in doomed}


def test_keep_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        gc.select_for_deletion([], keep=0)


def test_parse_and_paginate() -> None:
    pages = {
        1: [
            {
                "id": 7,
                "created_at": "2026-01-02T03:04:05Z",
                "metadata": {"container": {"tags": ["main"]}},
            }
        ],
        2: [
            {"id": 6, "created_at": "2026-01-01T00:00:00Z", "metadata": {"container": {"tags": []}}}
        ],
    }
    calls: list[str] = []

    def call(method: str, url: str) -> Any:
        calls.append(url)
        return pages.get(int(url.rsplit("=", 1)[1]), [])

    versions = gc.list_versions("https://api.github.com/users/o/packages/container/p", call)
    assert [(v.id, v.tags) for v in versions] == [(7, ("main",)), (6, ())]
    assert len(calls) == 3


def test_only_github_api_urls_are_called() -> None:
    with pytest.raises(ValueError, match="unexpected URL"):
        gc._request("GET", "https://example.com/x", "token")
