import pytest
import sqlite3
import threading
import time
import os
import uuid
from pathlib import Path
from unittest.mock import Mock, MagicMock
from datetime import datetime
from typing import Any, Dict, List, Optional

from iPhoto.domain.models import Album, Asset, MediaType
from iPhoto.infrastructure.repositories.sqlite_album_repository import SQLiteAlbumRepository
from iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from iPhoto.infrastructure.db.pool import ConnectionPool
from iPhoto.events.bus import EventBus
from iPhoto.application.use_cases.open_album import OpenAlbumUseCase
from iPhoto.application.use_cases.scan_album import ScanAlbumUseCase
from iPhoto.application.use_cases.pair_live_photos import PairLivePhotosUseCase
from iPhoto.application.dtos import OpenAlbumRequest, ScanAlbumRequest, PairLivePhotosRequest
from iPhoto.application.interfaces import IMetadataProvider, IThumbnailGenerator


class MockMetadataProvider(IMetadataProvider):
    """Mock metadata provider for testing."""
    
    def get_metadata_batch(self, paths: List[Path]) -> List[Dict[str, Any]]:
        return [{"SourceFile": str(p)} for p in paths]
    
    def normalize_metadata(self, root: Path, file_path: Path, raw_metadata: Dict[str, Any]) -> Dict[str, Any]:
        rel_path = file_path.relative_to(root)
        ext = file_path.suffix.lower()
        is_video = ext in ('.mp4', '.mov', '.avi', '.mkv')
        return {
            'id': f"as_{hash(str(rel_path)) % 1000000:06d}",
            'rel': str(rel_path),
            'bytes': file_path.stat().st_size if file_path.exists() else 0,
            'dt': None,
            'ts': int(file_path.stat().st_mtime * 1_000_000) if file_path.exists() else 0,
            'media_type': 1 if is_video else 0,
            'w': 100,
            'h': 100,
        }


class MockThumbnailGenerator(IThumbnailGenerator):
    """Mock thumbnail generator for testing."""
    
    def generate_micro_thumbnail(self, path: Path) -> Optional[str]:
        return None  # Skip thumbnail generation in tests

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


@pytest.fixture
def metadata_provider():
    return MockMetadataProvider()


@pytest.fixture
def thumbnail_generator():
    return MockThumbnailGenerator()


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


def test_asset_repository_maps_gps_column_into_metadata(asset_repo):
    with asset_repo._pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO assets (
                rel, id, album_id, media_type, bytes, gps
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("live/photo.heic", "asset-gps", "album-gps", 0, 123, '{"lat": 35.0, "lon": 139.0}'),
        )

    loaded = asset_repo.get("asset-gps")

    assert loaded is not None
    assert loaded.metadata.get("gps") == {"lat": 35.0, "lon": 139.0}


def test_save_uses_posix_paths_preventing_duplicates(asset_repo):
    """Regression test: save_batch must use POSIX paths (forward slashes) as PK
    to avoid creating duplicate rows when the DB already stores POSIX paths
    (e.g. from the legacy scanner)."""
    # 1. Insert an asset using POSIX path directly (simulating legacy scanner)
    with asset_repo._pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO assets (rel, id, album_id, media_type, bytes, is_favorite)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("subfolder/photo.jpg", "asset-posix", "album1", 0, 1024, 0),
        )

    # 2. Load the asset, toggle favorite, and re-save (simulating toggle_favorite flow)
    loaded = asset_repo.get("asset-posix")
    assert loaded is not None
    loaded.is_favorite = True
    asset_repo.save(loaded)

    # 3. Verify: there should be exactly ONE row, not two
    with asset_repo._pool.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE id = ?", ("asset-posix",)
        ).fetchone()[0]
        assert count == 1, f"Expected 1 row but found {count} — path separator caused duplicate"

    # 4. Verify the favorite flag was updated
    reloaded = asset_repo.get("asset-posix")
    assert reloaded.is_favorite is True

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

def test_scan_album_use_case(album_repo, asset_repo, event_bus, metadata_provider, thumbnail_generator, tmp_path):
    # Setup album
    album_path = tmp_path / "ScanTest"
    album_path.mkdir()
    (album_path / "photo1.jpg").touch()
    (album_path / "video1.mp4").touch()

    album = Album.create(path=album_path)
    album_repo.save(album)

    # Execute scan
    use_case = ScanAlbumUseCase(album_repo, asset_repo, event_bus, metadata_provider, thumbnail_generator)
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
