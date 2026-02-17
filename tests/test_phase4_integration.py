"""Tests for Phase 4 integration: ParallelScanner streaming, LibraryService, AssetService weak cache, bootstrap."""
import os
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iPhoto.application.services.parallel_scanner import ParallelScanner, ScanResult, _default_max_workers
from iPhoto.application.services.library_service import LibraryService
from iPhoto.application.services.asset_service import AssetService
from iPhoto.di.bootstrap import bootstrap
from iPhoto.di.container import Container
from iPhoto.domain.models.core import Album, Asset, MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanProgressEvent
from iPhoto.infrastructure.services.weak_asset_cache import WeakAssetCache


# -- Helpers --

def _make_asset(id="a1", path=Path("photo.jpg")):
    return Asset(
        id=id,
        album_id="album-1",
        path=path,
        media_type=MediaType.IMAGE,
        size_bytes=100,
    )


def _make_album_dir(tmp_path, n=10, ext=".jpg"):
    """Create *n* dummy media files under tmp_path."""
    for i in range(n):
        (tmp_path / f"img_{i:04d}{ext}").write_bytes(b"x" * 64)
    return tmp_path


# ========= ParallelScanner Enhancements =========

class TestParallelScannerCPUAware:
    def test_default_max_workers_is_half_cpus(self):
        cpus = os.cpu_count() or 2
        expected = max(1, cpus // 2)
        assert _default_max_workers() == expected

    def test_scanner_uses_default_workers_when_none(self):
        scanner = ParallelScanner(max_workers=None)
        assert scanner._max_workers == _default_max_workers()

    def test_cancel_aborts_scan(self, tmp_path):
        _make_album_dir(tmp_path, n=50)

        def slow_scan(p):
            time.sleep(0.05)
            return _make_asset(id=str(p))

        scanner = ParallelScanner(
            max_workers=2, batch_size=5, scan_file_fn=slow_scan, yield_interval=0
        )

        def cancel_soon():
            time.sleep(0.1)
            scanner.cancel()

        t = threading.Thread(target=cancel_soon)
        t.start()
        result = scanner.scan(tmp_path)
        t.join()

        assert scanner.is_cancelled
        assert result.total_processed < 50

    def test_scan_streaming_yields_batches(self, tmp_path):
        _make_album_dir(tmp_path, n=20)

        scanner = ParallelScanner(
            max_workers=2,
            batch_size=5,
            scan_file_fn=lambda p: _make_asset(id=str(p)),
            yield_interval=0,
        )

        batches = list(scanner.scan_streaming(tmp_path))
        assert len(batches) >= 1
        total_assets = sum(len(b.assets) for b in batches)
        assert total_assets == 20

    def test_progress_events_published(self, tmp_path):
        _make_album_dir(tmp_path, n=10)
        bus = EventBus()
        events = []
        bus.subscribe(ScanProgressEvent, events.append)

        scanner = ParallelScanner(
            max_workers=2,
            batch_size=5,
            event_bus=bus,
            scan_file_fn=lambda p: _make_asset(id=str(p)),
            yield_interval=0,
        )
        scanner.scan(tmp_path)

        # At least 2 progress events: one at batch boundary and one final
        assert len(events) >= 2


# ========= LibraryService Integration =========

class TestLibraryServiceParallelScan:
    def test_scan_album_parallel_returns_result(self, tmp_path):
        _make_album_dir(tmp_path, n=5)
        scanner = ParallelScanner(
            max_workers=1,
            batch_size=100,
            scan_file_fn=lambda p: _make_asset(id=str(p)),
            yield_interval=0,
        )
        svc = LibraryService(
            album_repo=MagicMock(),
            create_album_uc=MagicMock(),
            delete_album_uc=MagicMock(),
            parallel_scanner=scanner,
        )
        result = svc.scan_album_parallel(tmp_path)
        assert len(result.assets) == 5

    def test_scan_album_streaming_persists_batches(self, tmp_path):
        _make_album_dir(tmp_path, n=10)
        scanner = ParallelScanner(
            max_workers=1,
            batch_size=5,
            scan_file_fn=lambda p: _make_asset(id=str(p)),
            yield_interval=0,
        )
        asset_repo = MagicMock()
        svc = LibraryService(
            album_repo=MagicMock(),
            create_album_uc=MagicMock(),
            delete_album_uc=MagicMock(),
            parallel_scanner=scanner,
            asset_repo=asset_repo,
        )
        batches = list(svc.scan_album_streaming(tmp_path))
        assert sum(len(b.assets) for b in batches) == 10
        assert asset_repo.save_batch.call_count >= 1

    def test_no_scanner_returns_empty(self, tmp_path):
        svc = LibraryService(
            album_repo=MagicMock(),
            create_album_uc=MagicMock(),
            delete_album_uc=MagicMock(),
        )
        result = svc.scan_album_parallel(tmp_path)
        assert result.total_processed == 0

    def test_cancel_scan(self):
        scanner = MagicMock()
        svc = LibraryService(
            album_repo=MagicMock(),
            create_album_uc=MagicMock(),
            delete_album_uc=MagicMock(),
            parallel_scanner=scanner,
        )
        svc.cancel_scan()
        scanner.cancel.assert_called_once()


# ========= AssetService WeakCache =========

class TestAssetServiceWeakCache:
    def test_get_asset_cache_hit(self):
        repo = MagicMock()
        asset = _make_asset()
        repo.get.return_value = asset

        cache = WeakAssetCache(max_size=100)
        svc = AssetService(asset_repo=repo, weak_cache=cache)

        # First call populates cache
        result1 = svc.get_asset("a1")
        assert result1 is asset
        assert repo.get.call_count == 1

        # Second call should use cache (strong ref still held via 'asset')
        result2 = svc.get_asset("a1")
        assert result2 is asset
        assert repo.get.call_count == 1  # no additional repo call

    def test_toggle_favorite_invalidates_cache(self):
        repo = MagicMock()
        asset = _make_asset()
        asset.is_favorite = False
        repo.get.return_value = asset

        cache = MagicMock()
        svc = AssetService(asset_repo=repo, weak_cache=cache)
        svc.toggle_favorite("a1")
        cache.invalidate.assert_called_with("a1")
        assert cache.invalidate.call_count == 2  # before fetch + after save

    def test_works_without_cache(self):
        repo = MagicMock()
        repo.get.return_value = _make_asset()
        svc = AssetService(asset_repo=repo)
        assert svc.get_asset("a1") is not None


# ========= DI Bootstrap =========

class TestBootstrap:
    def test_bootstrap_registers_all_services(self):
        container = Container()
        bootstrap(container)

        from iPhoto.infrastructure.services.cache_stats import CacheStatsCollector
        from iPhoto.infrastructure.services.thumbnail_cache import MemoryThumbnailCache
        from iPhoto.infrastructure.services.memory_monitor import MemoryMonitor

        assert isinstance(container.resolve(EventBus), EventBus)
        assert isinstance(container.resolve(CacheStatsCollector), CacheStatsCollector)
        assert isinstance(container.resolve(MemoryThumbnailCache), MemoryThumbnailCache)
        assert isinstance(container.resolve(WeakAssetCache), WeakAssetCache)
        assert isinstance(container.resolve(MemoryMonitor), MemoryMonitor)

    def test_bootstrap_singletons_return_same_instance(self):
        container = Container()
        bootstrap(container)

        eb1 = container.resolve(EventBus)
        eb2 = container.resolve(EventBus)
        assert eb1 is eb2
