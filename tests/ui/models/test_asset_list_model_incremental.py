
import pytest
import json
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QModelIndex
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel
from src.iPhoto.gui.ui.models.roles import Roles
from src.iPhoto.cache.index_store import IndexStore
from src.iPhoto.config import WORK_DIR_NAME

# Mocks
class MockAlbum:
    def __init__(self, root):
        self.root = root
        self.manifest = {"featured": []}

class MockFacade(QObject):
    linksUpdated = Signal(Path)
    assetUpdated = Signal(Path)
    errorRaised = Signal(str)

    def __init__(self, root):
        super().__init__()
        self.current_album = MockAlbum(root)

@pytest.fixture
def app():
    if not QApplication.instance():
        return QApplication([])
    return QApplication.instance()

@pytest.fixture
def album_root(tmp_path):
    root = tmp_path / "MyAlbum"
    root.mkdir()
    (root / WORK_DIR_NAME).mkdir()
    return root

@pytest.fixture
def model(app, album_root):
    facade = MockFacade(album_root)
    model = AssetListModel(facade)
    return model, facade

def create_dummy_row(rel_path):
    return {
        "rel": rel_path,
        "abs": f"/absolute/{rel_path}",
        "id": rel_path,
        "is_image": True,
        "is_video": False,
        "is_live": False,
        "live_group_id": None,
        "live_motion": None,
        "live_motion_abs": None,
        "size": [100, 100],
        "dt": "2023-01-01T12:00:00",
        "featured": False,
        "bytes": 1024,
        "mime": "image/jpeg",
        "w": 100,
        "h": 100
    }

def test_incremental_update_appends_rows(app, album_root, model):
    asset_model, facade = model

    # Setup initial index with 10 items
    store = IndexStore(album_root)
    initial_rows = [create_dummy_row(f"img_{i:02d}.jpg") for i in range(10)]
    store.write_rows(initial_rows)

    # Load initial state
    asset_model.prepare_for_album(album_root)
    asset_model.populate_from_cache()

    assert asset_model.rowCount() == 10

    # Now simulate incremental update
    # Append 10 more rows
    new_rows = [create_dummy_row(f"img_{i:02d}.jpg") for i in range(10, 20)]
    store.append_rows(new_rows)

    # Verify store has 20 rows
    assert len(list(store.read_all())) == 20

    # Trigger update
    from PySide6.QtTest import QSignalSpy
    spy_inserted = QSignalSpy(asset_model.rowsInserted)

    facade.linksUpdated.emit(album_root)
    app.processEvents()

    assert asset_model.rowCount() == 20

    # Check signals
    print(f"Total insertion signals: {spy_inserted.count()}")
    for i in range(spy_inserted.count()):
        args = spy_inserted.at(i)
        print(f"Signal {i}: first={args[1]}, last={args[2]}")

    # We expect ONE signal for the batch of 10 items
    if spy_inserted.count() != 1:
        # If we failed, let's see what happened
        pytest.fail(f"Expected 1 insertion signal, got {spy_inserted.count()}")
    else:
        args = spy_inserted.at(0)
        assert args[1] == 10
        assert args[2] == 19

def test_insertion_order_at_top_bug(app, album_root, model):
    """
    Simulate the scenario where items are inserted at the TOP/index 0
    instead of the bottom, causing flickering/displacement of the first items.
    """
    asset_model, facade = model

    store = IndexStore(album_root)
    # Existing items
    initial_rows = [create_dummy_row(f"img_{i:02d}.jpg") for i in range(10)]
    store.write_rows(initial_rows)

    asset_model.prepare_for_album(album_root)
    asset_model.populate_from_cache()

    # New items (batch import)
    # Typically imports are appended to index.
    new_rows = [create_dummy_row(f"img_{i:02d}.jpg") for i in range(10, 20)]
    store.append_rows(new_rows)

    from PySide6.QtTest import QSignalSpy
    spy_inserted = QSignalSpy(asset_model.rowsInserted)

    facade.linksUpdated.emit(album_root)
    app.processEvents()

    # If they are inserted at 0, that's bad (unless sorted that way, but here we assume append)
    if spy_inserted.count() > 0:
        first_signal = spy_inserted.at(0)
        start_idx = first_signal[1]
        print(f"First insertion at index: {start_idx}")
        # If start_idx is 0, it means we are inserting at top.
        # But we appended to the store, so we expect insertion at 10.
        assert start_idx == 10

    # Verify order in model
    # Index 0 should still be img_00
    assert asset_model.data(asset_model.index(0, 0), Roles.REL) == "img_00.jpg"
    # Index 10 should be img_10
    assert asset_model.data(asset_model.index(10, 0), Roles.REL) == "img_10.jpg"
