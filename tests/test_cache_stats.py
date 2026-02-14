"""Tests for CacheStatsCollector — hit/miss tracking."""

from __future__ import annotations

import pytest

from iPhoto.infrastructure.services.cache_stats import CacheStats, CacheStatsCollector


class TestCacheStats:
    def test_empty(self):
        s = CacheStats()
        assert s.total == 0
        assert s.hit_rate == 0.0

    def test_all_hits(self):
        s = CacheStats(hits=10, misses=0)
        assert s.hit_rate == pytest.approx(1.0)

    def test_all_misses(self):
        s = CacheStats(hits=0, misses=10)
        assert s.hit_rate == pytest.approx(0.0)

    def test_mixed(self):
        s = CacheStats(hits=7, misses=3)
        assert s.hit_rate == pytest.approx(0.7)

    def test_total(self):
        s = CacheStats(hits=4, misses=6)
        assert s.total == 10


class TestCacheStatsCollector:
    def test_record_hit(self):
        c = CacheStatsCollector()
        c.record_hit("L1")
        assert c.get("L1").hits == 1
        assert c.get("L1").misses == 0

    def test_record_miss(self):
        c = CacheStatsCollector()
        c.record_miss("L1")
        assert c.get("L1").misses == 1
        assert c.get("L1").hits == 0

    def test_multiple_caches(self):
        c = CacheStatsCollector()
        c.record_hit("L1")
        c.record_hit("L1")
        c.record_miss("L2")
        assert c.get("L1").hits == 2
        assert c.get("L2").misses == 1

    def test_unknown_cache(self):
        c = CacheStatsCollector()
        s = c.get("nonexistent")
        assert s.hits == 0
        assert s.misses == 0
        assert s.hit_rate == 0.0

    def test_all(self):
        c = CacheStatsCollector()
        c.record_hit("L1")
        c.record_miss("L2")
        result = c.all()
        assert "L1" in result
        assert "L2" in result
        assert result["L1"].hits == 1
        assert result["L2"].misses == 1

    def test_reset_single(self):
        c = CacheStatsCollector()
        c.record_hit("L1")
        c.record_hit("L2")
        c.reset("L1")
        assert c.get("L1").hits == 0
        assert c.get("L2").hits == 1

    def test_reset_all(self):
        c = CacheStatsCollector()
        c.record_hit("L1")
        c.record_miss("L2")
        c.reset()
        assert c.get("L1").hits == 0
        assert c.get("L2").misses == 0

    def test_hit_rate_after_mixed(self):
        c = CacheStatsCollector()
        for _ in range(7):
            c.record_hit("L1")
        for _ in range(3):
            c.record_miss("L1")
        assert c.get("L1").hit_rate == pytest.approx(0.7)
