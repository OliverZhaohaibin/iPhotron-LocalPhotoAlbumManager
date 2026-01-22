import pytest
from unittest.mock import Mock, patch, ANY, call
from pathlib import Path
from datetime import datetime
import queue

from src.iPhoto.application.use_cases.scan_album import ScanAlbumUseCase, AlbumScannedEvent
from src.iPhoto.application.dtos import ScanAlbumRequest
from src.iPhoto.domain.models import Album, Asset, MediaType
from src.iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from src.iPhoto.events.bus import EventBus
from src.iPhoto.application.interfaces import IMetadataProvider, IThumbnailGenerator

@pytest.fixture
def mock_album_repo():
    return Mock(spec=IAlbumRepository)

@pytest.fixture
def mock_asset_repo():
    return Mock(spec=IAssetRepository)

@pytest.fixture
def mock_event_bus():
    return Mock(spec=EventBus)

@pytest.fixture
def mock_metadata():
    return Mock(spec=IMetadataProvider)

@pytest.fixture
def mock_thumbnails():
    return Mock(spec=IThumbnailGenerator)

@pytest.fixture
def use_case(mock_album_repo, mock_asset_repo, mock_event_bus, mock_metadata, mock_thumbnails):
    return ScanAlbumUseCase(mock_album_repo, mock_asset_repo, mock_event_bus, mock_metadata, mock_thumbnails)

def test_scan_new_files(use_case, mock_album_repo, mock_asset_repo, mock_metadata, mock_thumbnails, mock_event_bus, tmp_path):
    # Arrange
    album_path = tmp_path / "album"
    album_path.mkdir()

    # Create fake files
    (album_path / "img1.jpg").touch()
    (album_path / "img2.jpg").touch()

    album = Album(id="alb1", path=album_path, title="Album")
    mock_album_repo.get.return_value = album

    # No existing assets
    mock_asset_repo.get_by_album.return_value = []

    # Metadata stub
    mock_metadata.get_metadata_batch.return_value = [
        {"SourceFile": str(album_path / "img1.jpg")},
        {"SourceFile": str(album_path / "img2.jpg")}
    ]
    mock_metadata.normalize_metadata.side_effect = lambda r, p, m: {
        "id": f"hash_{p.name}",
        "rel": str(p.relative_to(album_path)),
        "bytes": 1000,
        "ts": 1600000000.0,
        "media_type": 0, # Image
        "w": 100, "h": 100
    }

    request = ScanAlbumRequest(album_id="alb1")

    # Act
    response = use_case.execute(request)

    # Assert
    assert response.added_count == 2
    assert response.updated_count == 0
    assert response.deleted_count == 0

    mock_asset_repo.save_batch.assert_called()
    # Check if save_batch was called with correct assets
    # We might have one or more calls depending on batch size (50)
    # Here 2 items < 50, so 1 call.
    args = mock_asset_repo.save_batch.call_args[0][0]
    assert len(args) == 2
    assert {a.path for a in args} == {Path("img1.jpg"), Path("img2.jpg")}

def test_scan_delete_missing(use_case, mock_album_repo, mock_asset_repo, mock_metadata, mock_thumbnails, tmp_path):
    # Arrange
    album_path = tmp_path / "album"
    album_path.mkdir()

    # Only 1 file exists on disk
    (album_path / "img1.jpg").touch()

    album = Album(id="alb1", path=album_path, title="Album")
    mock_album_repo.get.return_value = album

    # Repo has 2 assets: img1.jpg and img2.jpg (missing)
    existing_assets = [
        Asset(id="hash_img1.jpg", album_id="alb1", path=Path("img1.jpg"), media_type=MediaType.IMAGE, size_bytes=1000, created_at=datetime.now()),
        Asset(id="hash_img2.jpg", album_id="alb1", path=Path("img2.jpg"), media_type=MediaType.IMAGE, size_bytes=1000, created_at=datetime.now())
    ]
    mock_asset_repo.get_by_album.return_value = existing_assets

    mock_metadata.get_metadata_batch.return_value = [{"SourceFile": str(album_path / "img1.jpg")}]
    mock_metadata.normalize_metadata.return_value = {
        "id": "hash_img1.jpg", "rel": "img1.jpg", "bytes": 1000, "ts": 1600000000.0, "media_type": 0
    }

    request = ScanAlbumRequest(album_id="alb1")

    # Act
    response = use_case.execute(request)

    # Assert
    assert response.added_count == 0
    # Updated might depend on cache hit logic (timestamp check)
    # Since we didn't mock stat times perfectly matching, it might count as updated or skipped.
    # The current logic:
    #   if existing and ts/size match -> skip (count=0 updated)
    #   else -> updated (count=1 updated)
    # Let's assume mismatch for simplicity or check response

    assert response.deleted_count == 1
    mock_asset_repo.delete.assert_called_with("hash_img2.jpg")
