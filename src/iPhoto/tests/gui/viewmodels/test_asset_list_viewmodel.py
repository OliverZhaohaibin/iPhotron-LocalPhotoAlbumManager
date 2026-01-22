import pytest
from unittest.mock import Mock, ANY
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, QModelIndex

from src.iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
from src.iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.gui.ui.models.roles import Roles

@pytest.fixture
def mock_data_source():
    return Mock(spec=AssetDataSource)

@pytest.fixture
def view_model(mock_data_source):
    return AssetListViewModel(mock_data_source)

def test_load_album_populates_model(view_model, mock_data_source):
    # Arrange
    assets = [
        Asset(id="1", album_id="alb1", path=Path("img1.jpg"), media_type=MediaType.IMAGE, size_bytes=100),
        Asset(id="2", album_id="alb1", path=Path("vid1.mp4"), media_type=MediaType.VIDEO, size_bytes=200)
    ]
    mock_data_source.fetch_assets.return_value = assets

    # Act
    view_model.load_album("alb1")

    # Assert
    assert view_model.rowCount() == 2
    mock_data_source.fetch_assets.assert_called_with(query=ANY)

def test_data_returns_correct_values(view_model, mock_data_source):
    # Arrange
    dt = datetime.now()
    asset = Asset(
        id="1",
        album_id="alb1",
        path=Path("img1.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=100,
        created_at=dt,
        is_favorite=True
    )
    mock_data_source.fetch_assets.return_value = [asset]
    view_model.load_album("alb1")

    index = view_model.index(0, 0)

    # Act & Assert
    assert view_model.data(index, Roles.ASSET_ID) == "1"
    assert view_model.data(index, Roles.REL) == "img1.jpg"
    assert view_model.data(index, Roles.IS_IMAGE) is True
    assert view_model.data(index, Roles.IS_VIDEO) is False
    assert view_model.data(index, Roles.SIZE) == 100
    assert view_model.data(index, Roles.DT) == dt
    assert view_model.data(index, Roles.FEATURED) is True

def test_data_abs_path(view_model, mock_data_source):
    # Arrange
    asset = Asset(id="1", album_id="alb1", path=Path("sub/img.jpg"), media_type=MediaType.IMAGE, size_bytes=100)
    mock_data_source.fetch_assets.return_value = [asset]
    view_model.load_album("alb1")

    # Set library root
    lib_root = Path("/library")
    view_model.set_library_root(lib_root)

    index = view_model.index(0, 0)

    # Act
    abs_path = view_model.data(index, Roles.ABS)

    # Assert
    assert abs_path == str(lib_root / "sub/img.jpg")

def test_invalid_index_returns_none(view_model):
    assert view_model.data(QModelIndex(), Roles.ASSET_ID) is None
    assert view_model.data(view_model.index(999, 0), Roles.ASSET_ID) is None
