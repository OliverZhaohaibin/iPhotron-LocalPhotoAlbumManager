"""Three-tier thumbnail service — L1 memory → L2 disk → L3 generate."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Protocol

from iPhoto.infrastructure.services.thumbnail_cache import MemoryThumbnailCache
from iPhoto.infrastructure.services.disk_thumbnail_cache import DiskThumbnailCache

LOGGER = logging.getLogger(__name__)


class ThumbnailGenerator(Protocol):
    """Protocol for L3 thumbnail generators."""

    def generate(self, asset_id: str, size: tuple[int, int]) -> bytes | None: ...


class ThumbnailService:
    """Unified three-tier thumbnail cache entry point."""

    def __init__(
        self,
        memory_cache: MemoryThumbnailCache,
        disk_cache: DiskThumbnailCache,
        generator: ThumbnailGenerator,
        executor: ThreadPoolExecutor | None = None,
    ):
        self._l1 = memory_cache
        self._l2 = disk_cache
        self._generator = generator
        self._executor = executor or ThreadPoolExecutor(max_workers=2)

    @staticmethod
    def _make_key(asset_id: str, size: tuple[int, int]) -> str:
        return f"{asset_id}_{size[0]}x{size[1]}"

    def get_thumbnail(
        self,
        asset_id: str,
        size: tuple[int, int] = (256, 256),
    ) -> bytes | None:
        """Synchronous lookup: L1 → L2. Returns *None* on miss."""
        key = self._make_key(asset_id, size)

        # L1: memory
        data = self._l1.get(key)
        if data is not None:
            return data

        # L2: disk
        data = self._l2.get(key)
        if data is not None:
            self._l1.put(key, data)  # backfill L1
            return data

        return None  # caller should use request_thumbnail for async L3

    def request_thumbnail(
        self,
        asset_id: str,
        size: tuple[int, int],
        callback: Callable[[str, bytes], None],
    ) -> None:
        """Asynchronous request: generate via L3 and backfill L2 → L1."""
        self._executor.submit(self._generate_and_cache, asset_id, size, callback)

    def _generate_and_cache(
        self,
        asset_id: str,
        size: tuple[int, int],
        callback: Callable[[str, bytes], None],
    ) -> None:
        key = self._make_key(asset_id, size)
        try:
            data = self._generator.generate(asset_id, size)
        except Exception:
            LOGGER.exception("L3 thumbnail generation failed for %s", asset_id)
            return
        if data:
            self._l2.put(key, data)  # backfill L2
            self._l1.put(key, data)  # backfill L1
            callback(asset_id, data)
