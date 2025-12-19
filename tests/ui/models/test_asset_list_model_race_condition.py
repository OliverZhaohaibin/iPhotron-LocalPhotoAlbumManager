import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtCore import QObject, Signal
from pathlib import Path
from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel
from src.iPhoto.gui.ui.models.list_diff_calculator import ListDiffResult

# Mock classes to simulate the environment

class MockFacade(QObject):
    linksUpdated = Signal(Path)
    assetUpdated = Signal(Path)
    scanChunkReady = Signal(Path, list)
    errorRaised = Signal(str)

    def __init__(self):
        super().__init__()
        self.library_manager = MagicMock()
        self.library_manager.root.return_value = Path("/library/root")
        self.current_album = MagicMock()
        self.current_album.manifest = {}

class MockLoader(QObject):
    chunkReady = Signal(Path, list)
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)
    error = Signal(Path, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        # Wrap methods with MagicMock to track calls
        self.start = MagicMock(side_effect=self._start)
        self.cancel = MagicMock(side_effect=self._cancel)

    def _start(self, root, featured, filter_params=None):
        self._running = True

    def _cancel(self):
        self._running = False

    def is_running(self):
        return self._running

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
def model_setup():
    """Setup model with mocked dependencies."""
    facade = MockFacade()

    # Patch AssetDataLoader to use our MockLoader
    with patch('src.iPhoto.gui.ui.models.logic.ingestion_controller.AssetDataLoader', side_effect=MockLoader) as mock_loader_cls:
        # Also patch AssetCacheManager to avoid crashes in headless env
        with patch('src.iPhoto.gui.ui.models.asset_list_model.AssetCacheManager', side_effect=MockAssetCacheManager) as mock_cache_cls:
            model = AssetListModel(facade)

            # Access the instantiated mock loader
            mock_loader_instance = model._ingestion._data_loader

            yield model, mock_loader_instance

def test_race_condition_stale_chunk_ignored(model_setup):
    """
    Reproduce race condition:
    1. Start Load A (Filter A)
    2. Start Load B (Filter B) -> Cancels A
    3. Load A emits chunk -> Should be IGNORED
    4. Load A finishes -> Should NOT flush, should trigger restart if needed
    """
    model, loader = model_setup
    library_root = Path("/library/root")
    model.prepare_for_album(library_root)

    # 1. Start Load A (Filter: None)
    model.set_filter_mode(None)
    model.start_load()
    assert loader.start.called
    assert model._active_filter is None

    # Simulate that loader is running

    # 2. Start Load B (Filter: "videos")
    # This will internally call start_load -> cancel existing loader
    model.set_filter_mode("videos")

    assert model._active_filter == "videos"
    # verify cancel was called
    assert loader.cancel.called

    # 3. Simulate Stale Chunk from Load A
    # The loader is technically cancelled, but async threads might still emit a chunk.
    # The root is still the same library_root!
    stale_chunk = [{"rel": "photo_A.jpg", "abs": "/library/root/photo_A.jpg", "ts": 100}]

    # We want to check if this chunk is added.
    assert model.rowCount() == 0

    # Emit chunk
    loader.chunkReady.emit(library_root, stale_chunk)

    # CHECK: With the BUG, this chunk is added to the buffer or model.
    # With the FIX, it should be ignored.

    # Force flush buffer if any (on ingestion controller)
    if hasattr(model._ingestion, "_flush_pending_chunks"):
        model._ingestion._flush_pending_chunks()

    # Assert that the model does not accept a stale chunk (verifies the bug is fixed).
    if model.rowCount() > 0:
        pytest.fail("Race condition reproduced: Stale chunk was accepted!")

    # Patch QTimer to execute immediately
    with patch('src.iPhoto.gui.ui.models.logic.ingestion_controller.QTimer') as MockTimer:
        MockTimer.singleShot.side_effect = lambda ms, func: func()

        # 4. Simulate Load A Finished
        # This usually triggers the pending restart.
        loader.loadFinished.emit(library_root, True)

        # Check if start was called again (for the new filter)
        # loader.start should have been called twice in total: once for initial, once for retry.
        assert loader.start.call_count >= 2


def test_incremental_removal_prunes_cache(model_setup):
    model, _ = model_setup
    library_root = Path("/library/root")
    model.prepare_for_album(library_root)

    initial_rows = [
        {"rel": "keep.jpg", "abs": "/library/root/keep.jpg"},
        {"rel": "drop.jpg", "abs": "/library/root/drop.jpg"},
    ]

    model._repo.set_rows([dict(row) for row in initial_rows])
    model._repo.rebuild_lookup()

    model._cache_manager.reset_caches_for_new_rows.reset_mock()

    # Simulate removal via Diff
    diff = ListDiffResult()
    diff.removed_indices = [1]
    diff.structure_changed = True

    model._on_diff_ready(diff, [])

    # Verify reset_caches_for_new_rows was called with the remaining rows
    # The model._repo.rows should now contain only keep.jpg
    assert model.rowCount() == 1
    assert model._repo.rows[0]["rel"] == "keep.jpg"

    model._cache_manager.reset_caches_for_new_rows.assert_called_once()
