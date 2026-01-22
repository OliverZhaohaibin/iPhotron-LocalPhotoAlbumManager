import pytest
import sqlite3
from pathlib import Path
from datetime import datetime
from src.iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from src.iPhoto.infrastructure.db.pool import ConnectionPool
from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.domain.models.query import AssetQuery, SortOrder

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "global_index.db"

@pytest.fixture
def pool(db_path):
    return ConnectionPool(db_path)

@pytest.fixture
def repo(pool):
    return SQLiteAssetRepository(pool)

def create_dummy_asset(id, path_str, parent_album, album_id="album1"):
    return Asset(
        id=id,
        album_id=album_id,
        path=Path(path_str),
        media_type=MediaType.IMAGE,
        size_bytes=1000,
        parent_album_path=parent_album
    )

def test_save_and_get_asset(repo):
    asset = Asset(
        id="123",
        album_id="album1",
        path=Path("test/img.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=1024,
        created_at=datetime.now(),
        width=800,
        height=600,
        duration=None,
        metadata={"iso": 100},
        content_identifier="cid123",
        live_photo_group_id=None,
        is_favorite=True,
        parent_album_path="test"
    )
    repo.save(asset)

    fetched = repo.get("123")
    assert fetched is not None
    assert fetched.id == "123"
    assert fetched.path == Path("test/img.jpg")
    assert fetched.is_favorite is True
    assert fetched.metadata == {"iso": 100}

def test_find_by_query_album(repo):
    assets = [
        create_dummy_asset("1", "A/1.jpg", "A"),
        create_dummy_asset("2", "A/2.jpg", "A"),
        create_dummy_asset("3", "B/1.jpg", "B"),
    ]
    repo.save_batch(assets)

    query = AssetQuery(album_path="A")
    results = repo.find_by_query(query)
    assert len(results) == 2
    assert {a.id for a in results} == {"1", "2"}

def test_find_by_query_subalbums(repo):
    assets = [
        create_dummy_asset("1", "A/1.jpg", "A"),
        create_dummy_asset("2", "A/sub/2.jpg", "A/sub"),
        create_dummy_asset("3", "B/1.jpg", "B"),
    ]
    repo.save_batch(assets)

    # query with include_subalbums
    query = AssetQuery(album_path="A", include_subalbums=True)
    results = repo.find_by_query(query)
    assert len(results) == 2

    # query without include_subalbums
    query_exact = AssetQuery(album_path="A", include_subalbums=False)
    results_exact = repo.find_by_query(query_exact)
    assert len(results_exact) == 1
    assert results_exact[0].id == "1"

def test_count(repo):
    assets = [
        create_dummy_asset("1", "A/1.jpg", "A"),
        create_dummy_asset("2", "A/2.jpg", "A"),
    ]
    repo.save_batch(assets)

    count = repo.count(AssetQuery(album_path="A"))
    assert count == 2

    count_empty = repo.count(AssetQuery(album_path="Z"))
    assert count_empty == 0
