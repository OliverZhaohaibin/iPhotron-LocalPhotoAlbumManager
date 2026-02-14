"""Weak-reference cache for inactive asset objects.

Objects stored here are held via :class:`weakref.ref` so that the garbage
collector can reclaim them when no other strong references exist.  This
is ideal for caching asset metadata that may or may not be needed again:
if the caller still holds a reference the lookup is free; if not, the
entry silently disappears and will be re-fetched on next access.
"""

from __future__ import annotations

import threading
import weakref
from typing import TypeVar

T = TypeVar("T")


class WeakAssetCache:
    """Thread-safe weak-reference cache for arbitrary objects.

    Each value is stored as a :class:`weakref.ref`.  When the referent is
    garbage-collected the entry is automatically purged via a weak-ref
    callback, keeping the internal dict tidy.

    A :class:`threading.RLock` (reentrant) is used because the weak-ref
    ``_remove`` callback may fire while another method already holds the
    lock (e.g. the GC triggers during ``put``).

    Parameters
    ----------
    max_size:
        Maximum number of *live* entries.  When exceeded the oldest entry
        (by insertion order) is evicted.  ``0`` means unlimited.
    """

    def __init__(self, max_size: int = 0) -> None:
        self._max_size = max(0, max_size)
        self._data: dict[str, weakref.ref] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> object | None:
        """Return the cached object, or *None* if missing / collected."""
        with self._lock:
            ref = self._data.get(key)
            if ref is None:
                return None
            obj = ref()
            if obj is None:
                # Referent was collected — clean up stale entry
                self._data.pop(key, None)
                return None
            return obj

    def put(self, key: str, value: object) -> None:
        """Store *value* under *key* using a weak reference.

        Raises :class:`TypeError` if *value* does not support weak
        references (e.g. plain ``int``, ``str``, ``bytes``).
        """
        with self._lock:
            # Build a weak-ref with an _remove callback so that collected
            # entries are pruned automatically.
            def _remove(ref: weakref.ref, _key: str = key) -> None:
                with self._lock:
                    # Only pop if the ref is still the one we stored
                    if self._data.get(_key) is ref:
                        self._data.pop(_key, None)

            self._data[key] = weakref.ref(value, _remove)

            if self._max_size and len(self._data) > self._max_size:
                # Evict the oldest entry (first inserted key)
                oldest_key = next(iter(self._data))
                self._data.pop(oldest_key, None)

    def invalidate(self, key: str) -> None:
        """Remove *key* from the cache."""
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._data.clear()

    @property
    def size(self) -> int:
        """Number of entries whose referent is still alive."""
        with self._lock:
            return sum(1 for ref in self._data.values() if ref() is not None)

    @property
    def raw_size(self) -> int:
        """Number of entries including stale (collected) ones."""
        with self._lock:
            return len(self._data)
