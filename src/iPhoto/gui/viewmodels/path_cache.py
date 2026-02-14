"""Thread-safe LRU cache for filesystem path existence checks."""

from collections import OrderedDict
import os
import threading
from pathlib import Path

PATH_EXISTS_CACHE_LIMIT = 20_000


class PathExistsCache:
    """Bounded, thread-safe LRU cache that remembers os.path.exists() results."""

    def __init__(self, limit: int = PATH_EXISTS_CACHE_LIMIT) -> None:
        self._cache: OrderedDict[str, bool] = OrderedDict()
        self._lock = threading.Lock()
        self._limit = limit

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @staticmethod
    def path_exists(path: Path) -> bool:
        try:
            return path.exists()
        except OSError:
            return False

    @staticmethod
    def cache_key(path: Path) -> str:
        return os.path.normcase(str(path))

    def exists_cached(self, path: Path) -> bool:
        key = self.cache_key(path)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        exists = self.path_exists(path)
        with self._lock:
            self._cache[key] = exists
            if len(self._cache) > self._limit:
                self._cache.popitem(last=False)
        return exists
