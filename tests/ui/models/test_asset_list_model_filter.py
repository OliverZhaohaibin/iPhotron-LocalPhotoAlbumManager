from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel

class MockFacade(QObject):
    linksUpdated = Signal(Path)
    assetUpdated = Signal(Path)
    scanChunkReady = Signal(Path, list)
    errorRaised = Signal(str)

    def __init__(self):
        super().__init__()
        self._current_album = None

    @property
    def current_album(self):
        return self._current_album

class MockLoader(QObject):
    chunkReady = Signal(Path, list)
    loadFinished = Signal(Path, bool)
    loadProgress = Signal(Path, int, int)
    error = Signal(Path, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_filter_params = None
        self.last_root = None

    def start(self, root, featured, filter_params=None):
        self.last_root = root
        self.last_filter_params = filter_params

    def is_running(self):
        return False

    def cancel(self):
        pass

    def compute_rows(self, root, featured, filter_params=None):
        return [], 0

def test_asset_list_model_set_filter_mode(tmp_path):
    # Patch the loader creation inside AssetListModel
    with patch('src.iPhoto.gui.ui.models.asset_list_model.AssetDataLoader') as MockLoaderClass:
        mock_loader_instance = MockLoader()
        MockLoaderClass.return_value = mock_loader_instance

        # Patch dependencies that require full environment
        with patch('src.iPhoto.gui.ui.models.asset_list_model.AssetCacheManager') as MockCache:
            # Create model
            facade = MockFacade()
            model = AssetListModel(facade)

            # Setup initial state
            root = tmp_path / "album"
            model.prepare_for_album(root)

            # Test setting filter mode
            model.set_filter_mode("videos")

            assert model.active_filter_mode() == "videos"

            # Verify loader was started with correct params
            assert mock_loader_instance.last_root == root
            assert mock_loader_instance.last_filter_params is not None
            assert mock_loader_instance.last_filter_params["filter_mode"] == "videos"

            # Test changing filter mode
            model.set_filter_mode("live")
            assert model.active_filter_mode() == "live"
            assert mock_loader_instance.last_filter_params["filter_mode"] == "live"

            # Test clearing filter mode
            model.set_filter_mode(None)
            assert model.active_filter_mode() is None
            # When cleared, filter_params might be empty or missing mode key depending on implementation
            # Current impl: if self._active_filter: filter_params["filter_mode"] = ...
            assert "filter_mode" not in mock_loader_instance.last_filter_params
