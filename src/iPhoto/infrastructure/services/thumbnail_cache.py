"""L1: LRU in-memory thumbnail cache."""

from __future__ import annotations

from collections import OrderedDict


class MemoryThumbnailCache:
    """L1: LRU memory cache for thumbnail bytes.

    Evicts the least-recently-used entry when *max_size* is exceeded.
    """

    def __init__(self, max_size: int = 500):
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> bytes | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, data: bytes) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)  # evict oldest
        self._cache[key] = data

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def memory_usage_bytes(self) -> int:
        return sum(len(v) for v in self._cache.values())
