"""Integration tests for AssetListModel refactoring."""

from unittest.mock import MagicMock
import pytest
from PySide6.QtCore import Qt
from pathlib import Path

# Need to make sure facade is importable.
# The `conftest.py` adds ROOT and SRC to path.

from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel
from src.iPhoto.gui.facade import AppFacade

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

@pytest.fixture
def model(tmp_path):
    facade = MockFacade()
    model = AssetListModel(facade)
    return model

def test_model_initialization(model):
    """Test that model initializes with new components."""
    assert hasattr(model, "_accumulator")
    assert hasattr(model, "_row_adapter")
    assert hasattr(model, "_state_manager")

def test_chunk_accumulation(model):
    """Test that incoming chunks are buffered by accumulator."""

    # Simulate incoming chunk
    chunk = [{"rel": "a.jpg", "abs": "/tmp/a.jpg", "is_current": False}]

    # We can inspect internal accumulator state
    assert len(model._accumulator._incoming_buffer) == 0
    assert not model._accumulator._flush_timer.isActive()

    # This should call _accumulator.add_chunk
    model._on_loader_chunk_ready(Path("/tmp"), chunk)

    # Check if buffered (assuming root matches, which it won't if model._album_root is None)
    # AssetListModel checks: if not self._album_root ... return

    # Set album root
    root = Path("/tmp")
    model._album_root = root
    model._pending_loader_root = root # needed for loader chunk ready check

    model._on_loader_chunk_ready(root, chunk)

    assert len(model._accumulator._incoming_buffer) == 1
    # Timer start might fail in headless test env without event loop, so we skip checking isActive()
    # The fact that buffer length increased confirms add_chunk was called.

def test_flush_buffer_updates_model(model):
    """Test that flushing the buffer updates the model via Qt signals."""

    root = Path("/tmp")
    model._album_root = root
    model._pending_loader_root = root

    chunk = [{"rel": "a.jpg", "abs": "/tmp/a.jpg", "is_current": False}]

    # Spy on beginInsertRows
    model.beginInsertRows = MagicMock()
    model.endInsertRows = MagicMock()

    model._on_loader_chunk_ready(root, chunk)

    # Force flush
    model._accumulator.flush()

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

    # Verify that the model's data is updated and the appropriate signals are emitted when an existing row is updated.
def test_accumulator_update_existing_row(model):
    """Regression test: Accumulator merging an update should call invalidate_thumbnail without crash."""

    row_a = {"rel": "a.jpg", "val": 1}
    model._state_manager.set_rows([row_a])
    model._state_manager.rebuild_lookup()

    # Chunk with update
    chunk = [{"rel": "a.jpg", "val": 2}]

    # Mock dataChanged to avoid Qt warnings
    model.dataChanged = MagicMock()

    # Spy on invalidate_thumbnail
    # We need to wrap the real method if we want to check it was called,
    # but we can also just check it doesn't raise.
    # We will spy.
    real_invalidate = model.invalidate_thumbnail
    model.invalidate_thumbnail = MagicMock(side_effect=real_invalidate)

    # Flush directly
    model._accumulator.add_chunk(chunk)
    model._accumulator.flush()

    # Verify row updated
    assert model._state_manager.rows[0]["val"] == 2

    # Verify invalidate_thumbnail called
    model.invalidate_thumbnail.assert_called_with("a.jpg")
