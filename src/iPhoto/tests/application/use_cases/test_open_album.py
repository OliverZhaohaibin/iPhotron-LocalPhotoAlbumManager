import pytest
from unittest.mock import Mock, ANY
from pathlib import Path
from datetime import datetime

from src.iPhoto.application.use_cases.open_album import OpenAlbumUseCase, AlbumOpenedEvent
from src.iPhoto.application.dtos import OpenAlbumRequest
from src.iPhoto.domain.models import Album
from src.iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from src.iPhoto.events.bus import EventBus

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
def use_case(mock_album_repo, mock_asset_repo, mock_event_bus):
    return OpenAlbumUseCase(mock_album_repo, mock_asset_repo, mock_event_bus)

def test_execute_existing_album(use_case, mock_album_repo, mock_asset_repo, mock_event_bus):
    # Arrange
    path = Path("/photos/album1")
    existing_album = Album(
        id="alb1",
        path=path,
        title="Album 1",
        created_at=datetime.now()
    )
    mock_album_repo.get_by_path.return_value = existing_album
    mock_asset_repo.count.return_value = 10

    request = OpenAlbumRequest(path=path)

    # Act
    response = use_case.execute(request)

    # Assert
    assert response.album_id == "alb1"
    assert response.asset_count == 10
    mock_album_repo.save.assert_not_called()
    mock_event_bus.publish.assert_called_once()

    event = mock_event_bus.publish.call_args[0][0]
    assert isinstance(event, AlbumOpenedEvent)
    assert event.album_id == "alb1"
    assert event.path == path

def test_execute_new_album(use_case, mock_album_repo, mock_asset_repo, mock_event_bus):
    # Arrange
    path = Path("/photos/new_album")
    mock_album_repo.get_by_path.return_value = None # Not found
    mock_asset_repo.count.return_value = 0

    request = OpenAlbumRequest(path=path)

    # Act
    response = use_case.execute(request)

    # Assert
    assert response.album_id is not None
    assert response.title == "new_album"
    assert response.asset_count == 0

    # Verify save was called
    mock_album_repo.save.assert_called_once()
    saved_album = mock_album_repo.save.call_args[0][0]
    assert saved_album.path == path

    mock_event_bus.publish.assert_called_once()
