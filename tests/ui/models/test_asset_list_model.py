"""Integration tests for AssetListModel refactoring."""

from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import Qt, QObject, Signal
from pathlib import Path

# Need to make sure facade is importable.
# The `conftest.py` adds ROOT and SRC to path.

from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel

class MockFacade(MagicMock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.linksUpdated = MagicMock()
        self.assetUpdated = MagicMock()
        self.scanChunkReady = MagicMock()
        self.current_album = MagicMock()
        self.current_album.manifest = {}
        # Signal mocks need connect method
        self.linksUpdated.connect = MagicMock()
        self.assetUpdated.connect = MagicMock()
        self.scanChunkReady.connect = MagicMock()

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

@pytest.fixture
def model(tmp_path):
    facade = MockFacade()

    with patch('src.iPhoto.gui.ui.models.asset_list_model.AssetCacheManager', side_effect=MockAssetCacheManager):
        model = AssetListModel(facade)
        yield model

def test_model_initialization(model):
    """Test that model initializes with new components."""
    # AssetDataAccumulator was removed and replaced by direct buffering
    assert hasattr(model, "_pending_chunks_buffer")
    assert hasattr(model, "_row_adapter")
    assert hasattr(model, "_state_manager")

def test_chunk_accumulation(model):
    """Test that incoming chunks are buffered by internal buffer."""

    # Simulate incoming chunk
    chunk = [{"rel": "a.jpg", "abs": "/tmp/a.jpg", "is_current": False}]

    # We can inspect internal buffer state
    assert len(model._pending_chunks_buffer) == 0
    assert not model._flush_timer.isActive()

    # This should return early because album root is not set
    model._on_loader_chunk_ready(Path("/tmp"), chunk)
    assert len(model._pending_chunks_buffer) == 0

    # Set album root
    root = Path("/tmp")
    model._album_root = root
    model._pending_loader_root = root # needed for loader chunk ready check

    # Simulate first chunk (is_first_chunk is True by default)
    # First chunk is NOT buffered, it is applied immediately.
    model._on_loader_chunk_ready(root, chunk)
    assert len(model._pending_chunks_buffer) == 0
    assert model.rowCount() == 1

    # Simulate second chunk - should be buffered
    chunk2 = [{"rel": "b.jpg", "abs": "/tmp/b.jpg", "is_current": False}]
    model._on_loader_chunk_ready(root, chunk2)

    assert len(model._pending_chunks_buffer) == 1
    # Timer start might fail in headless test env without event loop, so we skip checking isActive()
    # The fact that buffer length increased confirms append was called.

def test_flush_buffer_updates_model(model):
    """Test that flushing the buffer updates the model via Qt signals."""

    root = Path("/tmp")
    model._album_root = root
    model._pending_loader_root = root

    # Make sure we are past the first chunk
    model._is_first_chunk = False

    chunk = [{"rel": "a.jpg", "abs": "/tmp/a.jpg", "is_current": False}]

    # Spy on beginInsertRows
    model.beginInsertRows = MagicMock()
    model.endInsertRows = MagicMock()

    model._on_loader_chunk_ready(root, chunk)
    assert len(model._pending_chunks_buffer) == 1

    # Force flush
    model._flush_pending_chunks()

    model.beginInsertRows.assert_called_once()
    model.endInsertRows.assert_called_once()
    assert model.rowCount() == 1

def test_data_retrieval_via_adapter(model):
    """Test that data() delegates to adapter."""

    root = Path("/tmp")
    model._album_root = root

    row_data = {"rel": "a.jpg", "abs": "/tmp/a.jpg", "is_current": True}

    # Manually set rows to bypass loader logic for simplicity
    model._state_manager.set_rows([row_data])

    index = model.index(0, 0)

    # Test adapter logic via model.data
    from src.iPhoto.gui.ui.models.roles import Roles

    assert model.data(index, Roles.REL) == "a.jpg"
    assert model.data(index, Roles.IS_CURRENT) is True
    assert model.data(index, Qt.DisplayRole) == ""

def test_apply_incremental_rows_via_diff_calculator(model):
    """Test that incremental updates use the diff calculator."""

    # Initial state
    row_a = {"rel": "a.jpg", "abs": "/tmp/a.jpg"}
    model._state_manager.set_rows([row_a])
    model._state_manager.rebuild_lookup()

    # New state: A removed, B inserted
    new_rows = [{"rel": "b.jpg", "abs": "/tmp/b.jpg"}]

    model.beginRemoveRows = MagicMock()
    model.endRemoveRows = MagicMock()
    model.beginInsertRows = MagicMock()
    model.endInsertRows = MagicMock()

    # Call protected method for testing
    model._apply_incremental_rows(new_rows)

    # A removed
    model.beginRemoveRows.assert_called_once()
    # B inserted
    model.beginInsertRows.assert_called_once()

    assert model.rowCount() == 1
    assert model._state_manager.rows[0]["rel"] == "b.jpg"

def test_incremental_update_existing_row(model):
    """Regression test: updating an existing row should not crash and should update data."""

    # Initial state: 2 rows
    row_a = {"rel": "a.jpg", "val": 1}
    row_b = {"rel": "b.jpg", "val": 1}
    model._state_manager.set_rows([row_a, row_b])
    model._state_manager.rebuild_lookup()

    # New state: B updated
    new_rows = [
        {"rel": "a.jpg", "val": 1},
        {"rel": "b.jpg", "val": 2}
    ]

    model.dataChanged = MagicMock()
    model.invalidate_thumbnail = MagicMock() # Mock it to verify calling; the real method exists now

    # Ensure real method exists (sanity check for regression fix)
    assert hasattr(model, "invalidate_thumbnail")

    # Call _apply_incremental_rows
    changed = model._apply_incremental_rows(new_rows)

    assert changed is True

    # Verify data updated in model
    assert model._state_manager.rows[1]["val"] == 2

    # Verify signal emitted
    # We expect dataChanged for row index 1
    # model.dataChanged.emit(index, index, [])
    # Since we mocked it, we can check calls.
    assert model.dataChanged.emit.call_count == 1
    args = model.dataChanged.emit.call_args[0]
    assert args[0].row() == 1

def test_buffer_add_new_row(model):
    """Regression test: Buffer merging should add new rows."""

    root = Path("/tmp")
    model._album_root = root
    model._pending_loader_root = root
    model._is_first_chunk = False # Buffer mode

    row_a = {"rel": "a.jpg", "val": 1}
    model._state_manager.set_rows([row_a])
    model._state_manager.rebuild_lookup()

    # Chunk with NEW item
    chunk = [{"rel": "b.jpg", "val": 2}]

    # Flush directly
    model._on_loader_chunk_ready(root, chunk)
    model._flush_pending_chunks()

    # Verify row added
    assert model.rowCount() == 2
    assert model._state_manager.rows[1]["val"] == 2
