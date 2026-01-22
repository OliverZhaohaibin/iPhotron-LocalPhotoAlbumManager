import pytest
from unittest.mock import Mock, patch
from pathlib import Path

from src.iPhoto.gui.facade import AppFacade
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.application.services.asset_service import AssetService
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.application.dtos import OpenAlbumResponse

@pytest.fixture
def mock_album_service():
    return Mock(spec=AlbumService)

@pytest.fixture
def mock_asset_service():
    return Mock(spec=AssetService)

@pytest.fixture
def mock_asset_repo():
    return Mock(spec=IAssetRepository)

@pytest.fixture
def facade():
    # Patch BackgroundTaskManager to avoid QThread issues in headless
    with patch("src.iPhoto.gui.facade.BackgroundTaskManager"), \
         patch("src.iPhoto.gui.facade.AssetImportService"), \
         patch("src.iPhoto.gui.facade.AssetMoveService"), \
         patch("src.iPhoto.gui.facade.LibraryUpdateService"), \
         patch("src.iPhoto.gui.facade.AlbumMetadataService"):
        f = AppFacade()
        return f

def test_set_services_switches_models(facade, mock_album_service, mock_asset_service, mock_asset_repo):
    # Initial state (legacy placeholder)
    assert facade._album_service is None

    # Inject services
    facade.set_services(mock_album_service, mock_asset_service, mock_asset_repo)

    assert facade._album_service is mock_album_service
    # Verify model is now AssetListViewModel
    from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
    assert isinstance(facade.asset_list_model, AssetListViewModel)

def test_open_album_uses_service(facade, mock_album_service, mock_asset_service, mock_asset_repo):
    # Setup mock repo to return empty list for assets
    mock_asset_repo.find_by_query.return_value = []

    facade.set_services(mock_album_service, mock_asset_service, mock_asset_repo)

    path = Path("/photos/album")
    mock_album_service.open_album.return_value = OpenAlbumResponse(
        album_id="alb1", title="Test Album", asset_count=10
    )

    # Patch Album.open because it reads FS
    with patch("src.iPhoto.models.album.Album.open") as mock_album_open:
        mock_album = Mock()
        mock_album.root = path
        mock_album.id = "alb1"
        mock_album_open.return_value = mock_album

        # Act
        result = facade.open_album(path)

        # Assert
        assert result == mock_album
        mock_album_service.open_album.assert_called_with(path)

        # Verify ViewModel loaded
        assert facade.asset_list_model._current_album_id == "alb1"
