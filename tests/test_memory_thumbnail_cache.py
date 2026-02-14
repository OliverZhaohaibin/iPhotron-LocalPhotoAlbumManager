"""Tests for MemoryThumbnailCache (L1 LRU cache)."""

from __future__ import annotations

import pytest

from iPhoto.infrastructure.services.thumbnail_cache import MemoryThumbnailCache


class TestMemoryThumbnailCache:
    def test_put_and_get(self):
        cache = MemoryThumbnailCache(max_size=10)
        cache.put("k1", b"data1")
        assert cache.get("k1") == b"data1"

    def test_get_miss(self):
        cache = MemoryThumbnailCache()
        assert cache.get("missing") is None

    def test_eviction_on_max_size(self):
        cache = MemoryThumbnailCache(max_size=2)
        cache.put("k1", b"d1")
        cache.put("k2", b"d2")
        cache.put("k3", b"d3")  # should evict k1
        assert cache.get("k1") is None
        assert cache.get("k2") == b"d2"
        assert cache.get("k3") == b"d3"

    def test_lru_eviction_order(self):
        cache = MemoryThumbnailCache(max_size=2)
        cache.put("k1", b"d1")
        cache.put("k2", b"d2")
        # Access k1 to make it recently used
        cache.get("k1")
        cache.put("k3", b"d3")  # should evict k2 (LRU)
        assert cache.get("k1") == b"d1"
        assert cache.get("k2") is None
        assert cache.get("k3") == b"d3"

    def test_put_updates_existing_key(self):
        cache = MemoryThumbnailCache(max_size=10)
        cache.put("k1", b"old")
        cache.put("k1", b"new")
        assert cache.get("k1") == b"new"
        assert cache.size == 1

    def test_invalidate(self):
        cache = MemoryThumbnailCache()
        cache.put("k1", b"data")
        cache.invalidate("k1")
        assert cache.get("k1") is None

    def test_invalidate_missing_key(self):
        cache = MemoryThumbnailCache()
        cache.invalidate("nope")  # should not raise

    def test_clear(self):
        cache = MemoryThumbnailCache()
        cache.put("a", b"1")
        cache.put("b", b"2")
        cache.clear()
        assert cache.size == 0
        assert cache.get("a") is None

    def test_size(self):
        cache = MemoryThumbnailCache()
        assert cache.size == 0
        cache.put("k1", b"x")
        assert cache.size == 1

    def test_memory_usage_bytes(self):
        cache = MemoryThumbnailCache()
        cache.put("k1", b"12345")
        cache.put("k2", b"67")
        assert cache.memory_usage_bytes == 7

    def test_max_size_one(self):
        cache = MemoryThumbnailCache(max_size=1)
        cache.put("k1", b"d1")
        cache.put("k2", b"d2")
        assert cache.get("k1") is None
        assert cache.get("k2") == b"d2"
        assert cache.size == 1
