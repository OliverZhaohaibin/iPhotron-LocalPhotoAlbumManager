import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from PySide6.QtCore import Qt, QModelIndex, QSize

from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
from src.iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from src.iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
from src.iPhoto.application.dtos import AssetDTO
from src.iPhoto.gui.ui.models.roles import Roles
from src.iPhoto.domain.models import MediaType
from src.iPhoto.domain.models.query import AssetQuery

@pytest.fixture
def mock_data_source():
    return MagicMock(spec=AssetDataSource)

@pytest.fixture
def mock_thumb_service():
    return MagicMock(spec=ThumbnailCacheService)

@pytest.fixture
def view_model(mock_data_source, mock_thumb_service):
    # Default count for mock is a MagicMock object, which isn't 0.
    # Set default behavior
    mock_data_source.count.return_value = 0
    return AssetListViewModel(mock_data_source, mock_thumb_service)

def test_viewmodel_init(view_model):
    assert view_model.rowCount() == 0

def test_load_query(view_model, mock_data_source):
    query = AssetQuery(album_path="test")
    view_model.load_query(query)
    mock_data_source.load.assert_called_with(query)

def test_row_count(view_model, mock_data_source):
    mock_data_source.count.return_value = 5
    assert view_model.rowCount() == 5

def test_data_display_role(view_model, mock_data_source):
    # Ensure rowCount > 0 so index is valid
    mock_data_source.count.return_value = 1

    asset = AssetDTO(
        id="1", abs_path=Path("/x/photo.jpg"), rel_path=Path("photo.jpg"),
        media_type="image", size_bytes=100,
        created_at=None, width=100, height=100,
        metadata={}, is_favorite=False, is_live=False, is_pano=False,
        duration=0.0
    )
    mock_data_source.asset_at.return_value = asset

    index = view_model.index(0, 0)
    result = view_model.data(index, Qt.DisplayRole)

    assert result == "photo.jpg"

def test_data_path_role(view_model, mock_data_source):
    mock_data_source.count.return_value = 1

    asset = AssetDTO(
        id="1", abs_path=Path("/full/path/photo.jpg"), rel_path=Path("photo.jpg"),
        media_type="image", size_bytes=100,
        created_at=None, width=100, height=100,
        metadata={}, is_favorite=False, is_live=False, is_pano=False,
        duration=0.0
    )
    mock_data_source.asset_at.return_value = asset

    index = view_model.index(0, 0)
    result = view_model.data(index, Roles.ABS)

    assert str(result) == str(asset.abs_path)

def test_data_thumbnail_role(view_model, mock_data_source, mock_thumb_service):
    mock_data_source.count.return_value = 1

    asset = AssetDTO(
        id="1", abs_path=Path("/x/photo.jpg"), rel_path=Path("photo.jpg"),
        media_type="image", size_bytes=100,
        created_at=None, width=100, height=100,
        metadata={}, is_favorite=False, is_live=False, is_pano=False,
        duration=0.0
    )
    mock_data_source.asset_at.return_value = asset

    # Mock pixmap return
    mock_pixmap = MagicMock()
    mock_thumb_service.get_thumbnail.return_value = mock_pixmap

    index = view_model.index(0, 0)
    result = view_model.data(index, Qt.DecorationRole)

    assert result == mock_pixmap
    mock_thumb_service.get_thumbnail.assert_called()

def test_get_qml_helper(view_model, mock_data_source):
    mock_data_source.count.return_value = 1

    asset = AssetDTO(
        id="1", abs_path=Path("/x/photo.jpg"), rel_path=Path("photo.jpg"),
        media_type="image", size_bytes=100,
        created_at=None, width=100, height=100,
        metadata={}, is_favorite=False, is_live=False, is_pano=False,
        duration=0.0
    )
    mock_data_source.asset_at.return_value = asset

    result = view_model.get(0)
    # get(row) returns data for Roles.ABS
    assert str(result) == str(asset.abs_path)

def test_invalid_index(view_model):
    result = view_model.data(QModelIndex(), Qt.DisplayRole)
    assert result is None
