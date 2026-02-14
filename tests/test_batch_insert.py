"""Tests for SQLiteAssetRepository.batch_insert with WAL mode."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from iPhoto.domain.models.core import Asset, MediaType
from iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from iPhoto.infrastructure.db.pool import ConnectionPool


def _make_asset(name: str, album_id: str = "album1") -> Asset:
    return Asset(
        id=name,
        album_id=album_id,
        path=Path(f"photos/{name}.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=1024,
    )


@pytest.fixture()
def repo(tmp_path: Path):
    db_path = tmp_path / "test.db"
    pool = ConnectionPool(str(db_path))
    return SQLiteAssetRepository(pool), pool


class TestBatchInsert:
    def test_batch_insert_returns_count(self, repo):
        repository, _ = repo
        assets = [_make_asset(f"a{i}") for i in range(5)]
        count = repository.batch_insert(assets)
        assert count == 5

    def test_batch_insert_empty_list(self, repo):
        repository, _ = repo
        assert repository.batch_insert([]) == 0

    def test_batch_insert_data_persisted(self, repo):
        repository, _ = repo
        assets = [_make_asset("img1"), _make_asset("img2")]
        repository.batch_insert(assets)
        # Verify via get
        a = repository.get("img1")
        assert a is not None
        assert a.id == "img1"

    def test_batch_insert_wal_mode(self, repo):
        repository, pool = repo
        assets = [_make_asset("wal1")]
        repository.batch_insert(assets, wal_mode=True)
        with pool.connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

    def test_batch_insert_without_wal(self, repo):
        repository, pool = repo
        assets = [_make_asset("nowal")]
        repository.batch_insert(assets, wal_mode=False)
        # Should still work, data should persist
        a = repository.get("nowal")
        assert a is not None

    def test_batch_insert_large_batch(self, repo):
        repository, _ = repo
        assets = [_make_asset(f"item{i}") for i in range(200)]
        count = repository.batch_insert(assets)
        assert count == 200
