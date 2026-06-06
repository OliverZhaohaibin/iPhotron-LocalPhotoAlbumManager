from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.cache.index_store.queries import QueryBuilder
from iPhoto.domain.models.query import AssetQuery, CollectionQuery, CollectionType, WindowResult
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore
from iPhoto.infrastructure.services.cache_stats import CacheStatsCollector
from iPhoto.infrastructure.services.disk_thumbnail_cache import DiskThumbnailCache
from iPhoto.infrastructure.services.thumbnail_cache import MemoryThumbnailCache
from iPhoto.infrastructure.services.thumbnail_service import ThumbnailService


SCAN_BASELINE_ROWS = 1_000
PAGINATION_BASELINE_ROWS = 2_500
THUMBNAIL_CACHE_HITS = 2_000
SCROLL_SANITY_ROWS = 10_000
SCAN_VISIBLE_PUBLISH_ROWS = 20

MAX_SCAN_SECONDS = 5.0
MAX_PAGINATION_SECONDS = 2.0
MAX_THUMBNAIL_CACHE_SECONDS = 1.0
MAX_SCROLL_SANITY_SECONDS = 2.0
MAX_VISIBLE_PUBLISH_SECONDS = 0.2


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


class _WindowQueryService:
    def __init__(self, library_root: Path, total_count: int) -> None:
        self.library_root = library_root
        self.total_count = total_count
        self.window_calls: list[tuple[int, int]] = []

    def read_query_asset_window(
        self,
        root: Path,
        query: AssetQuery,
        first: int,
        limit: int,
    ) -> WindowResult:
        del root, query
        first = max(0, min(int(first), max(0, self.total_count - 1)))
        limit = max(0, int(limit))
        self.window_calls.append((first, limit))
        end = min(self.total_count, first + limit)
        return WindowResult(
            first=first,
            rows=[_asset_row(index) for index in range(first, end)],
            total_count=self.total_count,
            collection_revision=len(self.window_calls),
        )

    def find_row_by_path(self, query: AssetQuery, path: Path) -> int | None:
        del query
        try:
            return int(path.stem.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            return None


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


def _ready_collection_row(index: int) -> dict[str, Any]:
    row = _asset_row(index)
    row.update(
        {
            "is_deleted": 0,
            "is_favorite": 1 if index % 3 == 0 else 0,
            "has_gps": 1 if index % 2 == 0 else 0,
            "media_type": 1 if index % 4 == 0 else 0,
            "thumbnail_state": "ready",
            "thumb_cache_key": f"thumb-{index:05d}",
        }
    )
    return row


def _assert_under_baseline(elapsed: float, limit: float, label: str) -> None:
    assert elapsed < limit, f"{label} took {elapsed:.3f}s; baseline limit is {limit:.3f}s"


def _collection_query_plan(repository: Any, query: CollectionQuery) -> str:
    sql, params = QueryBuilder.build_collection_query(query, limit=100)
    conn = repository._db_manager.get_connection()
    return " | ".join(
        " ".join(str(part) for part in row)
        for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    )


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


def test_ready_thumbnail_collection_queries_use_visible_indexes(tmp_path: Path) -> None:
    repository = get_global_repository(tmp_path)
    repository.write_rows(_ready_collection_row(index) for index in range(250))

    queries = [
        CollectionQuery(collection_type=CollectionType.ALL_PHOTOS),
        CollectionQuery(collection_type=CollectionType.ALBUM, album_path="Album"),
        CollectionQuery(collection_type=CollectionType.FAVORITES),
        CollectionQuery(collection_type=CollectionType.VIDEOS),
        CollectionQuery(collection_type=CollectionType.MAP, has_gps=True),
    ]

    for query in queries:
        sql, params = QueryBuilder.build_collection_query(query, limit=100)
        plan = _collection_query_plan(repository, query)

        assert "thumbnail_state = ?" in sql
        assert params[params.index("ready")] == "ready"
        assert "USING INDEX idx_assets_visible" in plan or "USING INDEX idx_assets_gps" in plan
        assert "USE TEMP B-TREE" not in plan


def test_gallery_scroll_window_materialization_bound(tmp_path: Path) -> None:
    service = _WindowQueryService(tmp_path, SCROLL_SANITY_ROWS)
    store = GalleryCollectionStore(service, tmp_path)
    store.load_selection(tmp_path, query=AssetQuery())

    started = time.perf_counter()
    for first in range(0, 5_000, 250):
        store.prioritize_rows(first, first + 79)
    elapsed = time.perf_counter() - started

    assert len(store._row_cache) <= store.MAX_WINDOW_SIZE + 1
    assert max(limit for _first, limit in service.window_calls) <= store.MAX_WINDOW_SIZE
    _assert_under_baseline(elapsed, MAX_SCROLL_SANITY_SECONDS, "gallery scroll window baseline")


def test_scan_visible_publish_latency_baseline() -> None:
    service = _WindowQueryService(Path("/library"), SCAN_VISIBLE_PUBLISH_ROWS)
    store = GalleryCollectionStore(service, Path("/library"))
    store.load_selection(Path("/library"), query=AssetQuery())
    store.prioritize_rows(0, min(SCAN_VISIBLE_PUBLISH_ROWS - 1, 19))
    batch = SimpleNamespace(
        root=Path("/library"),
        collection_revision=2,
        rows=[
            {
                "rel": f"Album/photo-{index:05d}.jpg",
                "id": f"asset-{index:05d}",
                "thumbnail_state": "ready",
                "thumb_cache_key": f"thumb-{index:05d}",
            }
            for index in range(SCAN_VISIBLE_PUBLISH_ROWS)
        ],
    )

    started = time.perf_counter()
    assert store.record_scan_batch(batch) is True
    store.flush_pending_scan_refresh()
    elapsed = time.perf_counter() - started

    assert store.snapshot_signature()[2] >= 2
    _assert_under_baseline(elapsed, MAX_VISIBLE_PUBLISH_SECONDS, "scan visible publish baseline")


@pytest.mark.skipif(
    os.environ.get("IPHOTO_RUN_STRESS") != "1",
    reason="Set IPHOTO_RUN_STRESS=1 to run 100k/1M synthetic scroll benchmarks.",
)
@pytest.mark.parametrize("row_count", [100_000, 1_000_000])
def test_stress_gallery_scroll_window_materialization_bound(tmp_path: Path, row_count: int) -> None:
    service = _WindowQueryService(tmp_path, row_count)
    store = GalleryCollectionStore(service, tmp_path)
    store.load_selection(tmp_path, query=AssetQuery())

    for first in range(0, min(row_count, 50_000), 1_000):
        store.prioritize_rows(first, first + 119)

    assert len(store._row_cache) <= store.MAX_WINDOW_SIZE + 1
    assert max(limit for _first, limit in service.window_calls) <= store.MAX_WINDOW_SIZE
