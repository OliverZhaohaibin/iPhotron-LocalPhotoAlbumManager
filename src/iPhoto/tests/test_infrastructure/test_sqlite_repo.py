import pytest
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from src.iPhoto.infrastructure.db.pool import ConnectionPool
from src.iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.domain.models.query import AssetQuery, SortOrder

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_db.sqlite"

@pytest.fixture
def pool(db_path):
    return ConnectionPool(db_path)

@pytest.fixture
def repo(pool):
    return SQLiteAssetRepository(pool)

@pytest.fixture
def sample_asset():
    return Asset(
        id="test_id_1",
        album_id="album_1",
        path=Path("album_1/photo.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=1024,
        created_at=datetime(2023, 1, 1, 12, 0, 0),
        width=1920,
        height=1080,
        duration=None,
        metadata={"iso": 100},
        content_identifier="cid_1",
        live_photo_group_id=None,
        is_favorite=False,
        parent_album_path="album_1"
    )

def test_repo_initialization(repo, db_path):
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'")
        assert cursor.fetchone() is not None

def test_save_and_get_asset(repo, sample_asset):
    repo.save(sample_asset)

    retrieved = repo.get(sample_asset.id)
    assert retrieved is not None
    assert retrieved.id == sample_asset.id
    assert retrieved.path == sample_asset.path
    assert retrieved.media_type == MediaType.IMAGE
    assert retrieved.metadata == sample_asset.metadata
    assert retrieved.parent_album_path == "album_1"

def test_update_asset(repo, sample_asset):
    repo.save(sample_asset)

    # Modify
    sample_asset.is_favorite = True
    repo.save(sample_asset)

    retrieved = repo.get(sample_asset.id)
    assert retrieved.is_favorite is True

def test_find_by_query_album(repo, sample_asset):
    repo.save(sample_asset)

    # Another asset in different album
    asset2 = Asset(
        id="test_id_2",
        album_id="album_2",
        path=Path("album_2/photo.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=2048,
        parent_album_path="album_2",
        created_at=datetime.now()
    )
    repo.save(asset2)

    query = AssetQuery(album_path="album_1")
    results = repo.find_by_query(query)

    assert len(results) == 1
    assert results[0].id == "test_id_1"

def test_find_by_query_media_type(repo):
    a1 = Asset(id="1", album_id="x", size_bytes=1, path=Path("p1"), media_type=MediaType.IMAGE, created_at=datetime.now())
    a2 = Asset(id="2", album_id="x", size_bytes=1, path=Path("p2"), media_type=MediaType.VIDEO, created_at=datetime.now())
    repo.save_batch([a1, a2])

    query = AssetQuery(media_types=[MediaType.VIDEO])
    results = repo.find_by_query(query)

    assert len(results) == 1
    assert results[0].id == "2"

def test_find_by_query_date_range(repo):
    d1 = datetime(2023, 1, 1)
    d2 = datetime(2023, 2, 1)
    d3 = datetime(2023, 3, 1)

    a1 = Asset(id="1", album_id="x", size_bytes=1, path=Path("p1"), media_type=MediaType.IMAGE, created_at=d1)
    a2 = Asset(id="2", album_id="x", size_bytes=1, path=Path("p2"), media_type=MediaType.IMAGE, created_at=d2)
    a3 = Asset(id="3", album_id="x", size_bytes=1, path=Path("p3"), media_type=MediaType.IMAGE, created_at=d3)

    repo.save_batch([a1, a2, a3])

    query = AssetQuery(date_from=datetime(2023, 1, 15), date_to=datetime(2023, 2, 15))
    results = repo.find_by_query(query)

    assert len(results) == 1
    assert results[0].id == "2"

def test_pagination(repo):
    assets = [
        Asset(id=str(i), album_id="x", size_bytes=1, path=Path(f"p{i}"), media_type=MediaType.IMAGE, created_at=datetime(2023, 1, 1, 0, i))
        for i in range(10)
    ]
    repo.save_batch(assets)

    # Page 1, size 3, ordered by creation (default is usually order_by='ts' DESC in query default)
    # Let's be explicit
    query = AssetQuery(limit=3, offset=0, order_by='created_at', order=SortOrder.ASC)
    results = repo.find_by_query(query)

    assert len(results) == 3
    assert results[0].id == "0"
    assert results[2].id == "2"

    # Page 2
    query.offset = 3
    results = repo.find_by_query(query)
    assert len(results) == 3
    assert results[0].id == "3"
