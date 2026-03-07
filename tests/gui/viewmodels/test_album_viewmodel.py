import pytest
from unittest.mock import MagicMock
from pathlib import Path
from PySide6.QtTest import QSignalSpy

from iPhoto.gui.viewmodels.album_viewmodel import AlbumViewModel
from iPhoto.application.services.album_service import AlbumService
from iPhoto.application.services.asset_service import AssetService
from iPhoto.domain.models import Album, Asset
from iPhoto.application.dtos import AlbumDTO

@pytest.fixture
def mock_album_service():
    return MagicMock(spec=AlbumService)

@pytest.fixture
def mock_asset_service():
    return MagicMock(spec=AssetService)

@pytest.fixture
def view_model(mock_album_service, mock_asset_service):
    return AlbumViewModel(mock_album_service, mock_asset_service)

def test_load_album_success(view_model, mock_album_service):
    # Arrange
    path = Path("/path/to/album")
    album_dto = AlbumDTO(id="123", path=path, name="Test Album", asset_count=10, cover_path=None)
    mock_album_service.open_album.return_value = album_dto

    # Spy on signal
    spy = QSignalSpy(view_model.albumLoaded)

    # Act
    view_model.load_album(path)

    # Assert
    mock_album_service.open_album.assert_called_with(path)
    assert spy.count() == 1
    assert spy.at(0)[0] == album_dto
    assert view_model._current_album_id == "123"

def test_load_album_failure(view_model, mock_album_service):
    # Arrange
    path = Path("/invalid")
    mock_album_service.open_album.side_effect = Exception("Album not found")

    spy = QSignalSpy(view_model.albumLoaded)

    # Act
    view_model.load_album(path)

    # Assert
    assert spy.count() == 0
    # Ideally should emit error signal, but current implementation logs error

def test_refresh_assets(view_model, mock_asset_service):
    # Arrange
    view_model._current_album_id = "123"
    mock_assets = [MagicMock(spec=Asset), MagicMock(spec=Asset)]
    mock_asset_service.find_assets.return_value = mock_assets

    spy = QSignalSpy(view_model.assetsLoaded)

    # Act
    view_model.refresh_assets()

    # Assert
    assert spy.count() == 1
    assert spy.at(0)[0] == mock_assets
    mock_asset_service.find_assets.assert_called()

def test_scan_current_album(view_model, mock_album_service):
    # Arrange
    view_model._current_album_id = "123"

    spy = QSignalSpy(view_model.scanFinished)

    # Act
    view_model.scan_current_album()

    # Assert
    mock_album_service.scan_album.assert_called_with("123")
    assert spy.count() == 1
