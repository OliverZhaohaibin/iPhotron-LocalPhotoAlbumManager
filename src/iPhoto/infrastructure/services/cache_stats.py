"""Cache hit-rate statistics collector.

Tracks ``hit`` / ``miss`` counts for named caches and exposes
hit-rate metrics.  Thread-safe; designed to be shared across
the three-tier thumbnail cache and any other caching layer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class CacheStats:
    """Immutable snapshot of cache statistics."""

    hits: int = 0
    misses: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        """Hit rate as a float in [0.0, 1.0]; 0.0 when no requests."""
        if self.total == 0:
            return 0.0
        return self.hits / self.total


class CacheStatsCollector:
    """Thread-safe collector for per-cache hit/miss counters.

    Usage::

        stats = CacheStatsCollector()
        stats.record_hit("L1")
        stats.record_miss("L1")
        print(stats.get("L1").hit_rate)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, int] = {}
        self._misses: dict[str, int] = {}

    def record_hit(self, cache_name: str) -> None:
        """Record a cache hit for *cache_name*."""
        with self._lock:
            self._hits[cache_name] = self._hits.get(cache_name, 0) + 1

    def record_miss(self, cache_name: str) -> None:
        """Record a cache miss for *cache_name*."""
        with self._lock:
            self._misses[cache_name] = self._misses.get(cache_name, 0) + 1

    def get(self, cache_name: str) -> CacheStats:
        """Return a :class:`CacheStats` snapshot for *cache_name*."""
        with self._lock:
            return CacheStats(
                hits=self._hits.get(cache_name, 0),
                misses=self._misses.get(cache_name, 0),
            )

    def all(self) -> dict[str, CacheStats]:
        """Return snapshots for every cache that has recorded data."""
        with self._lock:
            names = set(self._hits) | set(self._misses)
            return {
                name: CacheStats(
                    hits=self._hits.get(name, 0),
                    misses=self._misses.get(name, 0),
                )
                for name in sorted(names)
            }

    def reset(self, cache_name: str | None = None) -> None:
        """Reset counters.  If *cache_name* is ``None``, reset all."""
        with self._lock:
            if cache_name is None:
                self._hits.clear()
                self._misses.clear()
            else:
                self._hits.pop(cache_name, None)
                self._misses.pop(cache_name, None)
