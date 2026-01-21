import pytest
import sqlite3
import os
import uuid
from pathlib import Path
from datetime import datetime, timedelta

from src.iPhoto.domain.models import Album, Asset, MediaType
from src.iPhoto.domain.models.query import AssetQuery, SortOrder
from src.iPhoto.infrastructure.repositories.sqlite_album_repository import SQLiteAlbumRepository
from src.iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from src.iPhoto.infrastructure.db.pool import ConnectionPool
from src.iPhoto.events.bus import EventBus
from src.iPhoto.application.use_cases.scan_album import ScanAlbumUseCase
from src.iPhoto.application.dtos import ScanAlbumRequest
from src.iPhoto.di.container import DependencyContainer
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.application.services.asset_service import AssetService

@pytest.fixture
def db_pool(tmp_path):
    db_path = tmp_path / "comprehensive.db"
    return ConnectionPool(db_path)

@pytest.fixture
def asset_repo(db_pool):
    return SQLiteAssetRepository(db_pool)

@pytest.fixture
def album_repo(db_pool):
    return SQLiteAlbumRepository(db_pool)

@pytest.fixture
def event_bus():
    import logging
    return EventBus(logging.getLogger("test"))

# --- Repository Query Tests ---

def test_repository_query_filtering(asset_repo):
    # Setup data
    base_time = datetime(2023, 1, 1, 12, 0, 0)
    assets = [
        Asset(id="1", album_id="a1", path=Path("img1.jpg"), media_type=MediaType.IMAGE, size_bytes=100, created_at=base_time, is_favorite=True),
        Asset(id="2", album_id="a1", path=Path("vid1.mp4"), media_type=MediaType.VIDEO, size_bytes=200, created_at=base_time + timedelta(hours=1), is_favorite=False),
        Asset(id="3", album_id="a1", path=Path("img2.png"), media_type=MediaType.IMAGE, size_bytes=150, created_at=base_time + timedelta(hours=2), is_favorite=False),
        Asset(id="4", album_id="a2", path=Path("other.jpg"), media_type=MediaType.IMAGE, size_bytes=100, created_at=base_time, is_favorite=True),
    ]
    asset_repo.save_batch(assets)

    # Test 1: Filter by Album ID
    results = asset_repo.find_by_query(AssetQuery().with_album_id("a1"))
    assert len(results) == 3
    assert {a.id for a in results} == {"1", "2", "3"}

    # Test 2: Filter by Media Type
    results = asset_repo.find_by_query(AssetQuery().with_album_id("a1").only_images())
    assert len(results) == 2
    assert {a.id for a in results} == {"1", "3"}

    # Test 3: Filter by Favorite
    results = asset_repo.find_by_query(AssetQuery().with_album_id("a1").only_favorites())
    assert len(results) == 1
    assert results[0].id == "1"

    # Test 4: Date Range
    start = base_time + timedelta(minutes=30)
    end = base_time + timedelta(hours=1, minutes=30)
    results = asset_repo.find_by_query(AssetQuery(date_from=start, date_to=end))
    assert len(results) == 1
    assert results[0].id == "2"

def test_repository_pagination_sorting(asset_repo):
    base_time = datetime(2023, 1, 1, 10, 0, 0)
    assets = []
    for i in range(10):
        assets.append(Asset(
            id=str(i),
            album_id="sort_test",
            path=Path(f"{i}.jpg"),
            media_type=MediaType.IMAGE,
            size_bytes=100+i,
            created_at=base_time + timedelta(minutes=i)
        ))
    asset_repo.save_batch(assets)

    # Test 1: Sort ASC
    query = AssetQuery().with_album_id("sort_test")
    query.order_by = "created_at"
    query.order = SortOrder.ASC
    results = asset_repo.find_by_query(query)
    assert results[0].id == "0"
    assert results[-1].id == "9"

    # Test 2: Sort DESC
    query.order = SortOrder.DESC
    results = asset_repo.find_by_query(query)
    assert results[0].id == "9"
    assert results[-1].id == "0"

    # Test 3: Pagination
    query.paginate(page=2, page_size=3) # Offset 3, Limit 3. Items: 9, 8, 7, [6, 5, 4], 3...
    results = asset_repo.find_by_query(query)
    assert len(results) == 3
    assert results[0].id == "6"
    assert results[1].id == "5"
    assert results[2].id == "4"

    # Test 4: Count
    count = asset_repo.count(AssetQuery().with_album_id("sort_test"))
    assert count == 10

# --- Scanning Tests ---

def test_scan_updates_and_deletes(album_repo, asset_repo, event_bus, tmp_path):
    # Setup filesystem
    album_path = tmp_path / "ScanUpdate"
    album_path.mkdir()

    file1 = album_path / "keep.jpg"
    file1.touch()

    file2 = album_path / "delete.jpg"
    file2.touch()

    # Initial Scan
    album = Album.create(path=album_path)
    album_repo.save(album)

    uc = ScanAlbumUseCase(album_repo, asset_repo, event_bus)
    res1 = uc.execute(ScanAlbumRequest(album_id=album.id))
    assert res1.added_count == 2

    # Capture ID of 'keep.jpg'
    assets = asset_repo.get_by_album(album.id)
    keep_asset = next(a for a in assets if a.path.name == "keep.jpg")
    original_id = keep_asset.id

    # Modify filesystem: Delete one, add one, keep one
    file2.unlink() # Delete
    file3 = album_path / "new.jpg"
    file3.touch() # Add

    # Second Scan
    res2 = uc.execute(ScanAlbumRequest(album_id=album.id))

    assert res2.added_count == 1 # new.jpg
    assert res2.deleted_count == 1 # delete.jpg
    assert res2.updated_count == 1 # keep.jpg (re-verified)

    # Verify ID stability
    assets_v2 = asset_repo.get_by_album(album.id)
    keep_asset_v2 = next(a for a in assets_v2 if a.path.name == "keep.jpg")
    assert keep_asset_v2.id == original_id

    # Verify total count
    assert len(assets_v2) == 2 # keep + new

# --- DI Container Tests ---

def test_di_container_lifecycle():
    container = DependencyContainer()

    class Service:
        pass

    # Transient
    container.register(Service, factory=lambda: Service())
    s1 = container.resolve(Service)
    s2 = container.resolve(Service)
    assert s1 is not s2

    # Singleton
    container.register(Service, factory=lambda: Service(), singleton=True)
    s3 = container.resolve(Service)
    s4 = container.resolve(Service)
    assert s3 is s4

# --- Schema Migration Test ---

def test_schema_migration_adds_columns(tmp_path):
    db_path = tmp_path / "migration_test.db"

    # Create old schema manually
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE assets (
            id TEXT PRIMARY KEY,
            album_id TEXT,
            path TEXT,
            media_type TEXT,
            size_bytes INTEGER,
            created_at TEXT,
            width INTEGER,
            height INTEGER,
            duration REAL,
            metadata TEXT,
            content_identifier TEXT,
            live_photo_group_id TEXT
        )
    """)
    conn.close()

    # Initialize Repo - should trigger migration
    pool = ConnectionPool(db_path)
    repo = SQLiteAssetRepository(pool)

    # Check columns
    with pool.connection() as conn:
        cursor = conn.execute("PRAGMA table_info(assets)")
        columns = {row["name"] for row in cursor.fetchall()}
        assert "is_favorite" in columns
        assert "parent_album_path" in columns

        # Verify defaults
        conn.execute("INSERT INTO assets (id, album_id) VALUES ('1', 'a')")
        row = conn.execute("SELECT is_favorite FROM assets WHERE id='1'").fetchone()
        assert row["is_favorite"] == 0

# Add Metadata Persistence Test to tests/application/test_phase3_comprehensive.py
import json

def test_scan_preserves_metadata_on_update(album_repo, asset_repo, event_bus, tmp_path):
    # Setup
    album_path = tmp_path / "MetaPersist"
    album_path.mkdir()
    file1 = album_path / "photo.jpg"
    file1.touch()

    album = Album.create(path=album_path)
    album_repo.save(album)

    # 1. Initial Scan
    uc = ScanAlbumUseCase(album_repo, asset_repo, event_bus)
    uc.execute(ScanAlbumRequest(album_id=album.id))

    # 2. Enrich metadata manually (simulate background job)
    assets = asset_repo.get_by_album(album.id)
    asset = assets[0]
    asset.width = 1920
    asset.height = 1080
    asset.metadata = {"iso": 100}
    asset_repo.save(asset)

    # Verify enrichment
    reloaded = asset_repo.get(asset.id)
    assert reloaded.width == 1920
    assert reloaded.metadata == {"iso": 100}

    # 3. Re-scan (Update)
    # Touch file to force modification time update if logic checked mtime,
    # but here we update regardless if exists.
    # To be sure, let's update mtime.
    import time
    time.sleep(0.01)
    file1.touch()

    uc.execute(ScanAlbumRequest(album_id=album.id))

    # 4. Verify Metadata Persisted
    final = asset_repo.get(asset.id)
    assert final.width == 1920
    assert final.height == 1080
    assert final.metadata == {"iso": 100}
