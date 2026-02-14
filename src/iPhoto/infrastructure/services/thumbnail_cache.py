"""L1: LRU in-memory thumbnail cache."""

from __future__ import annotations

import threading
from collections import OrderedDict


class MemoryThumbnailCache:
    """L1: LRU memory cache for thumbnail bytes.

    Evicts the least-recently-used entry when *max_size* is exceeded.
    All public methods are protected by a lock so the cache can be
    accessed safely from multiple threads (e.g. the main thread calling
    ``get_thumbnail`` while a background executor runs ``request_thumbnail``).
    """

    def __init__(self, max_size: int = 500):
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key: str, data: bytes) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._max_size:
                    self._cache.popitem(last=False)  # evict oldest
            self._cache[key] = data

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    @property
    def memory_usage_bytes(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._cache.values())
