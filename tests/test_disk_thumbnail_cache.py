"""Tests for DiskThumbnailCache (L2 disk cache)."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.infrastructure.services.disk_thumbnail_cache import DiskThumbnailCache


class TestDiskThumbnailCache:
    def test_put_and_get(self, tmp_path: Path):
        cache = DiskThumbnailCache(tmp_path / "thumbs")
        cache.put("img_256x256", b"jpeg-bytes")
        assert cache.get("img_256x256") == b"jpeg-bytes"

    def test_get_miss(self, tmp_path: Path):
        cache = DiskThumbnailCache(tmp_path / "thumbs")
        assert cache.get("nonexistent") is None

    def test_creates_cache_dir(self, tmp_path: Path):
        cache_dir = tmp_path / "deep" / "cache"
        DiskThumbnailCache(cache_dir)
        assert cache_dir.exists()

    def test_hash_bucketing(self, tmp_path: Path):
        cache = DiskThumbnailCache(tmp_path / "thumbs")
        cache.put("some_key", b"data")
        # The file should be in a two-character hash bucket sub-directory
        files = list((tmp_path / "thumbs").rglob("*.jpg"))
        assert len(files) == 1
        # Verify bucketing: parent dir name should be 2 hex characters
        assert len(files[0].parent.name) == 2

    def test_overwrite(self, tmp_path: Path):
        cache = DiskThumbnailCache(tmp_path / "thumbs")
        cache.put("k1", b"old")
        cache.put("k1", b"new")
        assert cache.get("k1") == b"new"

    def test_invalidate(self, tmp_path: Path):
        cache = DiskThumbnailCache(tmp_path / "thumbs")
        cache.put("k1", b"data")
        cache.invalidate("k1")
        assert cache.get("k1") is None

    def test_invalidate_missing(self, tmp_path: Path):
        cache = DiskThumbnailCache(tmp_path / "thumbs")
        cache.invalidate("nope")  # should not raise

    def test_multiple_keys(self, tmp_path: Path):
        cache = DiskThumbnailCache(tmp_path / "thumbs")
        cache.put("k1", b"d1")
        cache.put("k2", b"d2")
        assert cache.get("k1") == b"d1"
        assert cache.get("k2") == b"d2"
