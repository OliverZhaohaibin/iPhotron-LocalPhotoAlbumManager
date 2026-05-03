from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.infrastructure.services.cache_stats import CacheStatsCollector
from iPhoto.infrastructure.services.disk_thumbnail_cache import DiskThumbnailCache
from iPhoto.infrastructure.services.thumbnail_cache import MemoryThumbnailCache
from iPhoto.infrastructure.services.thumbnail_service import ThumbnailService


SCAN_BASELINE_ROWS = 1_000
PAGINATION_BASELINE_ROWS = 2_500
THUMBNAIL_CACHE_HITS = 2_000

MAX_SCAN_SECONDS = 5.0
MAX_PAGINATION_SECONDS = 2.0
MAX_THUMBNAIL_CACHE_SECONDS = 1.0


class _SyntheticScanner:
    def __init__(self, count: int) -> None:
        self.count = count

    def scan(
        self,
        root: Path,
        include: Iterable[str],
        exclude: Iterable[str],
        *,
        existing_index: dict[str, dict[str, Any]] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        del root, include, exclude, existing_index
        if progress_callback is not None:
            progress_callback(0, self.count)

        base = datetime(2024, 1, 1)
        for index in range(self.count):
            timestamp = base + timedelta(seconds=index)
            yield {
                "rel": f"photo-{index:05d}.jpg",
                "id": f"asset-{index:05d}",
                "dt": timestamp.isoformat(),
                "ts": int(timestamp.timestamp() * 1_000_000),
                "bytes": 1024 + index,
                "media_type": 0,
                "mime": "image/jpeg",
                "live_role": 0,
            }

        if progress_callback is not None:
            progress_callback(self.count, self.count)


class _UnexpectedThumbnailGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, asset_id: str, size: tuple[int, int]) -> bytes | None:
        del asset_id, size
        self.calls += 1
        return b"generated"


@pytest.fixture(autouse=True)
def _reset_global_index() -> Iterator[None]:
    reset_global_repository()
    yield
    reset_global_repository()


def _asset_row(index: int) -> dict[str, Any]:
    timestamp = datetime(2024, 1, 1) + timedelta(seconds=index)
    return {
        "rel": f"Album/photo-{index:05d}.jpg",
        "id": f"asset-{index:05d}",
        "parent_album_path": "Album",
        "dt": timestamp.isoformat(),
        "ts": int(timestamp.timestamp() * 1_000_000),
        "bytes": 1024 + index,
        "media_type": 0,
        "mime": "image/jpeg",
        "live_role": 0,
    }


def _assert_under_baseline(elapsed: float, limit: float, label: str) -> None:
    assert elapsed < limit, f"{label} took {elapsed:.3f}s; baseline limit is {limit:.3f}s"


def test_scan_merge_performance_baseline(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Album"
    album_root.mkdir(parents=True)
    service = LibraryScanService(
        library_root,
        scanner=_SyntheticScanner(SCAN_BASELINE_ROWS),
    )

    started = time.perf_counter()
    result = service.scan_album(
        album_root,
        include=["*.jpg"],
        exclude=[],
        chunk_size=100,
        persist_chunks=True,
    )
    elapsed = time.perf_counter() - started

    repository = get_global_repository(library_root)
    assert len(result.rows) == SCAN_BASELINE_ROWS
    assert result.failed_count == 0
    assert repository.count(album_path="Album", include_subalbums=True) == SCAN_BASELINE_ROWS
    _assert_under_baseline(elapsed, MAX_SCAN_SECONDS, "scan merge baseline")


def test_gallery_pagination_performance_baseline(tmp_path: Path) -> None:
    repository = get_global_repository(tmp_path)
    repository.write_rows(_asset_row(index) for index in range(PAGINATION_BASELINE_ROWS))

    started = time.perf_counter()
    first_page = repository.get_assets_page(
        limit=100,
        album_path="Album",
        include_subalbums=True,
    )
    cursor = first_page[-1]
    second_page = repository.get_assets_page(
        cursor_dt=cursor["dt"],
        cursor_id=cursor["id"],
        limit=100,
        album_path="Album",
        include_subalbums=True,
    )
    elapsed = time.perf_counter() - started

    assert len(first_page) == 100
    assert len(second_page) == 100
    assert first_page[0]["id"] == "asset-02499"
    assert first_page[-1]["id"] == "asset-02400"
    assert second_page[0]["id"] == "asset-02399"
    _assert_under_baseline(elapsed, MAX_PAGINATION_SECONDS, "gallery pagination baseline")


def test_thumbnail_cache_hit_performance_baseline(tmp_path: Path) -> None:
    memory_cache = MemoryThumbnailCache(max_size=THUMBNAIL_CACHE_HITS + 1)
    disk_cache = DiskThumbnailCache(tmp_path / "thumbs")
    generator = _UnexpectedThumbnailGenerator()
    stats = CacheStatsCollector()
    service = ThumbnailService(memory_cache, disk_cache, generator, stats=stats)

    payload = b"thumb-bytes"
    key = ThumbnailService._make_key("asset-00001", (256, 256))
    disk_cache.put(key, payload)

    started = time.perf_counter()
    assert service.get_thumbnail("asset-00001", (256, 256)) == payload
    for _index in range(THUMBNAIL_CACHE_HITS):
        assert service.get_thumbnail("asset-00001", (256, 256)) == payload
    elapsed = time.perf_counter() - started

    assert generator.calls == 0
    assert stats.get("L2").hits == 1
    assert stats.get("L1").hits == THUMBNAIL_CACHE_HITS
    _assert_under_baseline(elapsed, MAX_THUMBNAIL_CACHE_SECONDS, "thumbnail cache baseline")
