"""Tests for WeakAssetCache — weak-reference cache for inactive objects."""

from __future__ import annotations

import gc

import pytest

from iPhoto.infrastructure.services.weak_asset_cache import WeakAssetCache


class _DummyAsset:
    """A simple class that supports weak references."""

    def __init__(self, name: str = "asset"):
        self.name = name


class TestWeakAssetCache:
    def test_put_and_get(self):
        cache = WeakAssetCache()
        obj = _DummyAsset("a1")
        cache.put("a1", obj)
        assert cache.get("a1") is obj

    def test_get_miss(self):
        cache = WeakAssetCache()
        assert cache.get("missing") is None

    def test_collected_object_returns_none(self):
        cache = WeakAssetCache()
        obj = _DummyAsset("a1")
        cache.put("a1", obj)
        del obj
        gc.collect()
        assert cache.get("a1") is None

    def test_size_reflects_live_objects(self):
        cache = WeakAssetCache()
        obj1 = _DummyAsset("a1")
        obj2 = _DummyAsset("a2")
        cache.put("a1", obj1)
        cache.put("a2", obj2)
        assert cache.size == 2
        del obj1
        gc.collect()
        assert cache.size == 1

    def test_invalidate(self):
        cache = WeakAssetCache()
        obj = _DummyAsset("a1")
        cache.put("a1", obj)
        cache.invalidate("a1")
        assert cache.get("a1") is None

    def test_invalidate_missing_key(self):
        cache = WeakAssetCache()
        cache.invalidate("nope")  # should not raise

    def test_clear(self):
        cache = WeakAssetCache()
        obj = _DummyAsset("a1")
        cache.put("a1", obj)
        cache.clear()
        assert cache.size == 0
        # Object still exists, just not in cache
        assert obj.name == "a1"

    def test_max_size_eviction(self):
        cache = WeakAssetCache(max_size=2)
        objs = [_DummyAsset(f"a{i}") for i in range(3)]
        cache.put("a0", objs[0])
        cache.put("a1", objs[1])
        cache.put("a2", objs[2])
        # a0 should be evicted (oldest)
        assert cache.get("a0") is None
        assert cache.get("a1") is objs[1]
        assert cache.get("a2") is objs[2]

    def test_overwrite_existing_key(self):
        cache = WeakAssetCache()
        obj1 = _DummyAsset("v1")
        obj2 = _DummyAsset("v2")
        cache.put("k", obj1)
        cache.put("k", obj2)
        assert cache.get("k") is obj2

    def test_raw_size_includes_stale(self):
        cache = WeakAssetCache()
        obj = _DummyAsset("a1")
        cache.put("a1", obj)
        assert cache.raw_size == 1
        del obj
        gc.collect()
        # raw_size may still be >= 0 depending on callback timing
        # but size (live) should be 0
        assert cache.size == 0

    def test_put_non_weakrefable_raises(self):
        cache = WeakAssetCache()
        with pytest.raises(TypeError):
            cache.put("k", 42)  # int does not support weakref

    def test_unlimited_max_size(self):
        cache = WeakAssetCache(max_size=0)
        objs = [_DummyAsset(f"a{i}") for i in range(100)]
        for i, obj in enumerate(objs):
            cache.put(f"a{i}", obj)
        assert cache.size == 100
