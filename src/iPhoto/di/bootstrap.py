"""DI container bootstrap — registers all application & infrastructure services."""

from __future__ import annotations

from .container import Container
from iPhoto.events.bus import EventBus
from iPhoto.infrastructure.services.cache_stats import CacheStatsCollector
from iPhoto.infrastructure.services.thumbnail_cache import MemoryThumbnailCache
from iPhoto.infrastructure.services.weak_asset_cache import WeakAssetCache
from iPhoto.infrastructure.services.memory_monitor import MemoryMonitor, GiB


def bootstrap(container: Container) -> None:
    """Register all application services in the DI container."""
    # Core
    container.register_singleton(EventBus, EventBus)

    # Performance: cache infrastructure
    container.register_singleton(CacheStatsCollector, CacheStatsCollector)
    container.register_singleton(MemoryThumbnailCache, MemoryThumbnailCache, max_size=500)
    container.register_singleton(WeakAssetCache, WeakAssetCache, max_size=5000)

    # Performance: memory monitor with sensible defaults
    container.register_singleton(
        MemoryMonitor,
        MemoryMonitor,
        warning_bytes=1 * GiB,
        critical_bytes=2 * GiB,
    )
