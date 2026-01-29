import pytest
import sqlite3
import threading
import time
import os
import uuid
from pathlib import Path
from unittest.mock import Mock, MagicMock
from datetime import datetime

from src.iPhoto.domain.models import Album, Asset, MediaType
from src.iPhoto.infrastructure.repositories.sqlite_album_repository import SQLiteAlbumRepository
from src.iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from src.iPhoto.infrastructure.db.pool import ConnectionPool
from src.iPhoto.events.bus import EventBus
from src.iPhoto.application.use_cases.open_album import OpenAlbumUseCase
from src.iPhoto.application.use_cases.scan_album import ScanAlbumUseCase
from src.iPhoto.application.use_cases.pair_live_photos import PairLivePhotosUseCase
from src.iPhoto.application.dtos import OpenAlbumRequest, ScanAlbumRequest, PairLivePhotosRequest

@pytest.fixture
def db_pool(tmp_path):
    db_path = tmp_path / "test.db"
    pool = ConnectionPool(db_path)
    return pool

@pytest.fixture
def album_repo(db_pool):
    return SQLiteAlbumRepository(db_pool)

@pytest.fixture
def asset_repo(db_pool):
    return SQLiteAssetRepository(db_pool)

@pytest.fixture
def event_bus():
    return EventBus()

# --- Repository Tests ---

def test_album_repository_save_get(album_repo, tmp_path):
    album = Album.create(path=tmp_path / "MyAlbum", title="My Album")
    album_repo.save(album)

    loaded = album_repo.get(album.id)
    assert loaded is not None
    assert loaded.id == album.id
    assert loaded.title == "My Album"
    assert loaded.path == tmp_path / "MyAlbum"

def test_asset_repository_save_get(asset_repo, tmp_path):
    asset = Asset(
        id="asset1",
        album_id="album1",
        path=Path("photo.jpg"),
        media_type=MediaType.PHOTO,
        size_bytes=1024,
        created_at=datetime.now()
    )
    asset_repo.save(asset)

    loaded = asset_repo.get("asset1")
    assert loaded is not None
    assert loaded.path == Path("photo.jpg")
    assert loaded.media_type == MediaType.PHOTO

# --- Use Case Tests ---

def test_open_album_use_case(album_repo, asset_repo, event_bus, tmp_path):
    use_case = OpenAlbumUseCase(album_repo, asset_repo, event_bus)
    album_path = tmp_path / "TestAlbum"
    album_path.mkdir()

    response = use_case.execute(OpenAlbumRequest(path=album_path))

    assert response.title == "TestAlbum"

    # Verify persistence
    saved_album = album_repo.get(response.album_id)
    assert saved_album is not None

def test_scan_album_use_case(album_repo, asset_repo, event_bus, tmp_path):
    # Setup album
    album_path = tmp_path / "ScanTest"
    album_path.mkdir()
    (album_path / "photo1.jpg").touch()
    (album_path / "video1.mp4").touch()

    album = Album.create(path=album_path)
    album_repo.save(album)

    # Execute scan
    use_case = ScanAlbumUseCase(album_repo, asset_repo, event_bus)
    response = use_case.execute(ScanAlbumRequest(album_id=album.id))

    assert response.added_count == 2

    assets = asset_repo.get_by_album(album.id)
    assert len(assets) == 2
    paths = {str(a.path) for a in assets}
    assert "photo1.jpg" in paths
    assert "video1.mp4" in paths

def test_pair_live_photos_use_case(album_repo, asset_repo, event_bus):
    album_id = "album1"

    # Setup assets
    assets = [
        Asset(id="1", album_id=album_id, path=Path("img.jpg"), media_type=MediaType.PHOTO, size_bytes=0),
        Asset(id="2", album_id=album_id, path=Path("img.mov"), media_type=MediaType.VIDEO, size_bytes=0),
        Asset(id="3", album_id=album_id, path=Path("other.jpg"), media_type=MediaType.PHOTO, size_bytes=0),
    ]
    asset_repo.save_all(assets)

    use_case = PairLivePhotosUseCase(asset_repo, event_bus)
    response = use_case.execute(PairLivePhotosRequest(album_id=album_id))

    assert response.paired_count == 1

    # Verify
    p1 = asset_repo.get("1")
    p2 = asset_repo.get("2")
    p3 = asset_repo.get("3")

    assert p1.live_photo_group_id is not None
    assert p1.live_photo_group_id == p2.live_photo_group_id
    assert p3.live_photo_group_id is None

def test_pair_live_photos_different_folders(album_repo, asset_repo, event_bus):
    album_id = "album1"

    # Setup assets with same name but different folders
    assets = [
        Asset(id="1", album_id=album_id, path=Path("folder1/img.jpg"), media_type=MediaType.PHOTO, size_bytes=0),
        Asset(id="2", album_id=album_id, path=Path("folder2/img.mov"), media_type=MediaType.VIDEO, size_bytes=0),
    ]
    asset_repo.save_all(assets)

    use_case = PairLivePhotosUseCase(asset_repo, event_bus)
    response = use_case.execute(PairLivePhotosRequest(album_id=album_id))

    # Should not pair
    assert response.paired_count == 0

    p1 = asset_repo.get("1")
    p2 = asset_repo.get("2")
    assert p1.live_photo_group_id is None
    assert p2.live_photo_group_id is None
