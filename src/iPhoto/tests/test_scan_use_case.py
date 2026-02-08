import pytest
from unittest.mock import MagicMock, Mock
from pathlib import Path
from src.iPhoto.application.use_cases.scan_album import ScanAlbumUseCase, AlbumScannedEvent
from src.iPhoto.application.dtos import ScanAlbumRequest
from src.iPhoto.domain.models import Album, Asset
from src.iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from src.iPhoto.events.bus import EventBus
from src.iPhoto.application.interfaces import IMetadataProvider, IThumbnailGenerator
from datetime import datetime

@pytest.fixture
def album_repo():
    return Mock(spec=IAlbumRepository)

@pytest.fixture
def asset_repo():
    return Mock(spec=IAssetRepository)

@pytest.fixture
def event_bus():
    return Mock(spec=EventBus)

@pytest.fixture
def metadata_provider():
    return Mock(spec=IMetadataProvider)

@pytest.fixture
def thumbnail_generator():
    return Mock(spec=IThumbnailGenerator)

@pytest.fixture
def scan_use_case(album_repo, asset_repo, event_bus, metadata_provider, thumbnail_generator):
    return ScanAlbumUseCase(album_repo, asset_repo, event_bus, metadata_provider, thumbnail_generator)

def test_scan_album_success(scan_use_case, album_repo, asset_repo, event_bus, metadata_provider, tmp_path):
    # Setup
    album_id = "test_album"
    album_path = tmp_path / "test_album"
    album_path.mkdir()
    (album_path / "photo.jpg").touch()

    # Mock Album
    mock_album = Mock()
    mock_album.id = album_id
    mock_album.path = album_path
    album_repo.get.return_value = mock_album

    asset_repo.get_by_album.return_value = []

    # Metadata provider mock
    metadata_provider.get_metadata_batch.return_value = [{"SourceFile": str(album_path / "photo.jpg")}]
    metadata_provider.normalize_metadata.return_value = {
        "id": "as_123",
        "rel": "photo.jpg",
        "bytes": 0,
        "ts": 1000,
        "media_type": 0,
        "mime": "image/jpeg"
    }

    # Execute
    request = ScanAlbumRequest(album_id=album_id)
    response = scan_use_case.execute(request)

    # Assert
    assert response.added_count == 1
    asset_repo.save_batch.assert_called_once()
    event_bus.publish.assert_called_once()
    call_args = event_bus.publish.call_args[0][0]
    assert isinstance(call_args, AlbumScannedEvent)
    assert call_args.added_count == 1

def test_scan_album_update_preserves_id(scan_use_case, album_repo, asset_repo, event_bus, metadata_provider, tmp_path):
    # Setup
    album_id = "test_album"
    album_path = tmp_path / "test_album"
    album_path.mkdir()
    photo_path = album_path / "photo.jpg"
    photo_path.touch()

    # Existing asset
    existing_asset = Asset(
        id="existing_id_123",
        album_id=album_id,
        path=Path("photo.jpg"),
        media_type="photo",
        size_bytes=100, # Old size
        created_at=datetime.fromtimestamp(1000),
        is_favorite=True # Should be preserved
    )

    mock_album = Mock()
    mock_album.id = album_id
    mock_album.path = album_path
    album_repo.get.return_value = mock_album

    asset_repo.get_by_album.return_value = [existing_asset]

    # Metadata provider returns NEW hash ID but same path
    metadata_provider.get_metadata_batch.return_value = [{"SourceFile": str(photo_path)}]
    metadata_provider.normalize_metadata.return_value = {
        "id": "as_new_hash_456", # Changed content hash
        "rel": "photo.jpg",
        "bytes": 200, # Changed size
        "ts": 2000, # Changed ts
        "media_type": 0,
        "mime": "image/jpeg"
    }

    # Execute
    request = ScanAlbumRequest(album_id=album_id)
    response = scan_use_case.execute(request)

    # Assert
    assert response.updated_count == 1
    asset_repo.save_batch.assert_called_once()
    saved_assets = asset_repo.save_batch.call_args[0][0]
    assert len(saved_assets) == 1
    saved_asset = saved_assets[0]

    # CRITICAL: ID should be preserved from existing asset
    assert saved_asset.id == "existing_id_123"
    # CRITICAL: Favorite status should be preserved
    assert saved_asset.is_favorite == True
    # Verify properties updated
    assert saved_asset.size_bytes == 200

def test_scan_album_move_preserves_asset(scan_use_case, album_repo, asset_repo, event_bus, metadata_provider, tmp_path):
    # Setup: Existing asset at old_path, but file is now at new_path (Move scenario)
    album_id = "test_album"
    album_path = tmp_path / "test_album"
    album_path.mkdir()

    # File is at new location
    new_path = album_path / "new_folder" / "photo.jpg"
    new_path.parent.mkdir()
    new_path.touch()

    # Asset record points to old location
    existing_asset = Asset(
        id="same_id_123", # ID based on content hash
        album_id=album_id,
        path=Path("old_folder/photo.jpg"),
        media_type="photo",
        size_bytes=100,
        created_at=datetime.fromtimestamp(1000),
        is_favorite=True
    )

    mock_album = Mock()
    mock_album.id = album_id
    mock_album.path = album_path
    album_repo.get.return_value = mock_album

    # Repo returns existing asset (at old path)
    asset_repo.get_by_album.return_value = [existing_asset]

    # Metadata provider sees file at new path, but SAME ID (content hash unchanged)
    metadata_provider.get_metadata_batch.return_value = [{"SourceFile": str(new_path)}]
    metadata_provider.normalize_metadata.return_value = {
        "id": "same_id_123", # Same ID as existing
        "rel": "new_folder/photo.jpg",
        "bytes": 100,
        "ts": 1000,
        "media_type": 0,
        "mime": "image/jpeg"
    }

    # Execute
    request = ScanAlbumRequest(album_id=album_id)
    response = scan_use_case.execute(request)

    # Assert
    # 1. New asset (at new path) should be saved
    asset_repo.save_batch.assert_called_once()
    saved_assets = asset_repo.save_batch.call_args[0][0]
    assert len(saved_assets) == 1
    assert saved_assets[0].path == Path("new_folder/photo.jpg")
    assert saved_assets[0].id == "same_id_123"

    # 2. CRITICAL: Old asset (at old path) should NOT be deleted because its ID was encountered at the new path
    asset_repo.delete.assert_not_called()

    # 3. Counts
    assert response.added_count == 1 # It's technically "added" because path is new to the map?
    # Logic in code: if existing: updated else added.
    # existing check is based on path map. old path not in found paths. new path not in existing map.
    # So it treats it as a new addition.
    # AND deletion check: old path not in found paths -> candidate for delete.
    # But ID check prevents delete.
    # So result is: We have a record for new path with same ID. We KEEP record for old path?
    # Wait, if we keep old record and add new record with same ID (PK),
    # save_batch uses INSERT OR REPLACE. So it overwrites the old record if ID is PK?
    # If ID is PK, then it's an update.
    # If we insert "new_folder/photo.jpg" with ID "123", it replaces "old_folder/photo.jpg" with ID "123".
    # So effectively it IS a move.

    # However, delete() takes ID. If we called delete(123), it would delete the row we just upserted?
    # That's why we must ensure delete(123) is NOT called.

    assert response.deleted_count == 0
