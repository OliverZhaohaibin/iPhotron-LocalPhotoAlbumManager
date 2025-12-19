"""Integration tests for AssetListModel refactoring."""

from unittest.mock import MagicMock, patch, call
import pytest
from PySide6.QtCore import Qt, QObject, Signal
from pathlib import Path

from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel
from src.iPhoto.gui.ui.models.roles import Roles
from src.iPhoto.gui.ui.models.list_diff_calculator import ListDiffResult

class MockFacade(MagicMock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.linksUpdated = MagicMock()
        self.assetUpdated = MagicMock()
        self.scanChunkReady = MagicMock()
        self.current_album = MagicMock()
        self.current_album.manifest = {}
        self.linksUpdated.connect = MagicMock()
        self.assetUpdated.connect = MagicMock()
        self.scanChunkReady.connect = MagicMock()
        self.errorRaised = MagicMock()
        self.library_manager = MagicMock()
        self.library_manager.root.return_value = Path("/lib")

class MockAssetCacheManager(QObject):
    thumbnailReady = Signal(Path, str, object)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.set_library_root = MagicMock()
        self.reset_for_album = MagicMock()
        self.clear_recently_removed = MagicMock()
        self.set_recently_removed_limit = MagicMock()
        self.reset_caches_for_new_rows = MagicMock()
        self.remove_thumbnail = MagicMock()
        self.remove_placeholder = MagicMock()
        self.remove_recently_removed = MagicMock()
        self.thumbnail_for = MagicMock(return_value=None)
        self.resolve_thumbnail = MagicMock()
        self.recently_removed = MagicMock(return_value=None)
        self.thumbnail_loader = MagicMock()
        self.clear_placeholders = MagicMock()
        self.clear_all_thumbnails = MagicMock()
        self.clear_visible_rows = MagicMock()

@pytest.fixture
def model(tmp_path):
    facade = MockFacade()
    with patch('src.iPhoto.gui.ui.models.asset_list_model.AssetCacheManager', side_effect=MockAssetCacheManager):
        model = AssetListModel(facade)
        yield model

def test_model_initialization(model):
    """Test that model initializes with new components."""
    assert hasattr(model, "_repo")
    assert hasattr(model, "_ingestion")
    assert hasattr(model, "_sync")
    # Facade signals connected
    model._facade.assetUpdated.connect.assert_called_with(model.handle_asset_updated)

def test_ingestion_integration(model):
    """Test that model inserts rows when ingestion controller emits batchReady."""

    batch = [{"rel": "a.jpg", "abs": "/tmp/a.jpg", "is_current": False}]

    # Simulate signal from ingestion controller
    # Since we didn't mock ingestion controller in fixture, it's real (or we should mock it).
    # If it's real, we can call the slot directly or emit the signal.
    # The slot is _on_ingestion_batch_ready

    model._on_ingestion_batch_ready(batch)

    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, Roles.REL) == "a.jpg"

def test_diff_integration_insert(model):
    """Test that model applies insertions from diff."""
    # Initial state empty

    diff = ListDiffResult()
    diff.inserted_items = [(0, {"rel": "a.jpg", "abs": "/tmp/a.jpg"}, "a.jpg")]

    model._on_diff_ready(diff, [])

    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), Roles.REL) == "a.jpg"

def test_diff_integration_remove(model):
    """Test that model applies removals from diff."""
    # Initial state
    model._repo.set_rows([{"rel": "a.jpg", "abs": "/tmp/a.jpg"}])
    assert model.rowCount() == 1

    diff = ListDiffResult()
    diff.removed_indices = [0]

    model._on_diff_ready(diff, [])

    assert model.rowCount() == 0

def test_diff_integration_update(model):
    """Test that model applies updates from diff."""
    # Initial state
    model._repo.set_rows([{"rel": "a.jpg", "val": 1}])

    diff = ListDiffResult()
    diff.changed_items = [{"rel": "a.jpg", "val": 2}]

    # Spy on dataChanged
    with patch.object(model, 'dataChanged') as mock_signal:
        model._on_diff_ready(diff, [])

        assert model._repo.rows[0]["val"] == 2
        assert mock_signal.emit.called

def test_data_retrieval_via_adapter(model):
    """Test that data() delegates to adapter."""

    row_data = {"rel": "a.jpg", "abs": "/tmp/a.jpg", "is_current": True}
    model._repo.set_rows([row_data])

    index = model.index(0, 0)

    assert model.data(index, Roles.REL) == "a.jpg"
    assert model.data(index, Roles.IS_CURRENT) is True

def test_invalidate_thumbnail_integration(model):
    """Test invalidate_thumbnail updates cache and emits signal."""
    row_data = {"rel": "a.jpg", "abs": "/tmp/a.jpg"}
    model._repo.set_rows([row_data])
    model._repo.rebuild_lookup()

    with patch.object(model, 'dataChanged') as mock_signal:
        model.invalidate_thumbnail("a.jpg")

        model._cache_manager.remove_thumbnail.assert_called_with("a.jpg")
        assert mock_signal.emit.called

def test_set_filter_mode_resets_model(model):
    """Test that setting filter mode triggers reset and load."""
    model._album_root = Path("/tmp")
    model._ingestion.start_load = MagicMock()

    model.set_filter_mode("videos")

    assert model.active_filter_mode() == "videos"
    assert model._ingestion.start_load.called
