import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from PySide6.QtCore import Qt, QModelIndex, QSize

from iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
from iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.domain.models.query import AssetQuery

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


def _make_dto(**overrides) -> AssetDTO:
    """Create a minimal AssetDTO for testing."""
    defaults = dict(
        id="1",
        abs_path=Path("photo.jpg"),
        rel_path=Path("photo.jpg"),
        media_type="image",
        created_at=None,
        width=100,
        height=100,
        duration=0.0,
        size_bytes=100,
        metadata={},
        is_favorite=False,
    )
    defaults.update(overrides)
    return AssetDTO(**defaults)


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

    dto = _make_dto(rel_path=Path("photo.jpg"))
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    result = view_model.data(index, Qt.DisplayRole)

    assert result == "photo.jpg"

def test_data_path_role(view_model, mock_data_source):
    mock_data_source.count.return_value = 1

    dto = _make_dto(abs_path=Path("/full/path/photo.jpg"))
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    result = view_model.data(index, Roles.ABS)

    # On linux/mac this is posix, win is nt.
    assert str(result).endswith("photo.jpg")

def test_data_thumbnail_role(view_model, mock_data_source, mock_thumb_service):
    mock_data_source.count.return_value = 1

    dto = _make_dto()
    mock_data_source.asset_at.return_value = dto

    # Mock pixmap return
    mock_pixmap = MagicMock()
    mock_thumb_service.get_thumbnail.return_value = mock_pixmap

    index = view_model.index(0, 0)
    result = view_model.data(index, Qt.DecorationRole)

    assert result == mock_pixmap
    mock_thumb_service.get_thumbnail.assert_called()

def test_get_qml_helper(view_model, mock_data_source):
    mock_data_source.count.return_value = 1

    dto = _make_dto(abs_path=Path("photo.jpg"))
    mock_data_source.asset_at.return_value = dto

    result = view_model.get(0)
    assert str(result) == "photo.jpg"

def test_invalid_index(view_model):
    result = view_model.data(QModelIndex(), Qt.DisplayRole)
    assert result is None


def test_unchanged_count_skips_reset(view_model, mock_data_source):
    mock_data_source.count.return_value = 2
    view_model._last_snapshot = (2, b"sig")
    with (
        patch.object(view_model, "beginResetModel") as begin_reset,
        patch.object(view_model, "endResetModel") as end_reset,
        patch.object(view_model, "_snapshot_hash", return_value=b"sig"),
    ):
        view_model._on_source_changed()
    begin_reset.assert_not_called()
    end_reset.assert_not_called()


def test_changed_count_triggers_reset(view_model, mock_data_source):
    mock_data_source.count.return_value = 1
    view_model._last_snapshot = (0, b"sig")
    with (
        patch.object(view_model, "beginResetModel") as begin_reset,
        patch.object(view_model, "endResetModel") as end_reset,
        patch.object(view_model, "_snapshot_hash", return_value=b"new"),
    ):
        view_model._on_source_changed()
    begin_reset.assert_called_once()
    end_reset.assert_called_once()


def test_changed_paths_triggers_reset(view_model, mock_data_source):
    mock_data_source.count.return_value = 2
    view_model._last_snapshot = (2, b"old")
    with (
        patch.object(view_model, "beginResetModel") as begin_reset,
        patch.object(view_model, "endResetModel") as end_reset,
        patch.object(view_model, "_snapshot_hash", return_value=b"new"),
    ):
        view_model._on_source_changed()
    begin_reset.assert_called_once()
    end_reset.assert_called_once()
