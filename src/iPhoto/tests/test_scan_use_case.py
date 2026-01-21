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
