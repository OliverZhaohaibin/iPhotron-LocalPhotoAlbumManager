"""Tests for ThumbnailService — three-tier cache (L1 → L2 → L3)."""

from __future__ import annotations

import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from iPhoto.infrastructure.services.thumbnail_cache import MemoryThumbnailCache
from iPhoto.infrastructure.services.disk_thumbnail_cache import DiskThumbnailCache
from iPhoto.infrastructure.services.thumbnail_service import ThumbnailService


# ---------------------------------------------------------------------------
# Stub generator
# ---------------------------------------------------------------------------

class StubGenerator:
    """Generates deterministic fake thumbnail bytes."""

    def __init__(self, data: bytes = b"generated-thumb"):
        self._data = data

    def generate(self, asset_id: str, size: tuple[int, int]) -> bytes | None:
        return self._data


class FailingGenerator:
    def generate(self, asset_id: str, size: tuple[int, int]) -> bytes | None:
        raise RuntimeError("generation failed")


class NoneGenerator:
    def generate(self, asset_id: str, size: tuple[int, int]) -> bytes | None:
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestThumbnailService:
    @pytest.fixture()
    def service(self, tmp_path: Path):
        mem = MemoryThumbnailCache(max_size=100)
        disk = DiskThumbnailCache(tmp_path / "thumbs")
        gen = StubGenerator()
        return ThumbnailService(
            memory_cache=mem,
            disk_cache=disk,
            generator=gen,
            executor=ThreadPoolExecutor(max_workers=1),
        ), mem, disk

    # -- L1 hit -----------------------------------------------------------

    def test_l1_hit(self, service):
        svc, mem, _disk = service
        mem.put("asset1_256x256", b"from-l1")
        assert svc.get_thumbnail("asset1", (256, 256)) == b"from-l1"

    # -- L2 hit (backfills L1) --------------------------------------------

    def test_l2_hit_backfills_l1(self, service):
        svc, mem, disk = service
        disk.put("asset2_256x256", b"from-l2")
        result = svc.get_thumbnail("asset2", (256, 256))
        assert result == b"from-l2"
        # Verify L1 was backfilled
        assert mem.get("asset2_256x256") == b"from-l2"

    # -- Cache miss -------------------------------------------------------

    def test_miss_returns_none(self, service):
        svc, _mem, _disk = service
        assert svc.get_thumbnail("missing") is None

    # -- request_thumbnail (L3 async) ------------------------------------

    def test_request_thumbnail_generates_and_caches(self, tmp_path: Path):
        mem = MemoryThumbnailCache()
        disk = DiskThumbnailCache(tmp_path / "thumbs")
        gen = StubGenerator(data=b"generated")
        svc = ThumbnailService(
            memory_cache=mem,
            disk_cache=disk,
            generator=gen,
            executor=ThreadPoolExecutor(max_workers=1),
        )

        done = threading.Event()
        received: list[tuple[str, bytes]] = []

        def _callback(asset_id: str, data: bytes):
            received.append((asset_id, data))
            done.set()

        svc.request_thumbnail("a1", (128, 128), _callback)
        done.wait(timeout=5)

        assert len(received) == 1
        assert received[0] == ("a1", b"generated")
        # Verify backfill
        assert mem.get("a1_128x128") == b"generated"
        assert disk.get("a1_128x128") == b"generated"

    def test_request_thumbnail_failing_generator(self, tmp_path: Path):
        mem = MemoryThumbnailCache()
        disk = DiskThumbnailCache(tmp_path / "thumbs")
        gen = FailingGenerator()
        svc = ThumbnailService(
            memory_cache=mem,
            disk_cache=disk,
            generator=gen,
            executor=ThreadPoolExecutor(max_workers=1),
        )

        done = threading.Event()

        def _callback(asset_id: str, data: bytes):
            done.set()

        svc.request_thumbnail("a1", (128, 128), _callback)
        # Callback should NOT be invoked
        assert not done.wait(timeout=1)

    def test_request_thumbnail_none_result(self, tmp_path: Path):
        mem = MemoryThumbnailCache()
        disk = DiskThumbnailCache(tmp_path / "thumbs")
        gen = NoneGenerator()
        svc = ThumbnailService(
            memory_cache=mem,
            disk_cache=disk,
            generator=gen,
            executor=ThreadPoolExecutor(max_workers=1),
        )

        done = threading.Event()

        def _callback(asset_id: str, data: bytes):
            done.set()

        svc.request_thumbnail("a1", (128, 128), _callback)
        assert not done.wait(timeout=1)

    # -- Key format -------------------------------------------------------

    def test_key_format(self):
        key = ThumbnailService._make_key("abc", (128, 256))
        assert key == "abc_128x256"
