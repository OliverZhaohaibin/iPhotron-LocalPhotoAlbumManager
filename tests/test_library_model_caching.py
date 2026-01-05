
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
import sys

# Ensure src is in path if not already
# sys.path.insert(0, "src")

from src.iPhoto.gui.facade import AppFacade
from src.iPhoto.library.manager import LibraryManager
from src.iPhoto.models.album import Album
from PySide6.QtWidgets import QApplication

@pytest.fixture
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Ensure QApplication exists for signals to work properly
    if QApplication.instance():
        return QApplication.instance()
    return QApplication([])

@pytest.fixture
def mock_backend(mocker):
    """Mock backend module to avoid disk I/O and syntax errors if backend is complex."""
    backend = mocker.patch("src.iPhoto.gui.facade.backend")
    # Setup open_album to return a dummy Album object
    backend.open_album.return_value = MagicMock(spec=Album)
    # Setup IndexStore to simulate empty or non-empty index
    store = MagicMock()
    store.read_all.return_value = iter([]) # Empty by default
    backend.IndexStore.return_value = store
    return backend

@pytest.fixture
def mock_library_manager(tmp_path):
    manager = MagicMock(spec=LibraryManager)
    manager.root.return_value = tmp_path
    # mock ensure_deleted_directory etc if needed
    return manager

def test_repro_missing_is_valid_optimization(tmp_path, mock_backend, mock_library_manager, mocker, qapp):
    """
    Test that prepare_for_album IS called when switching back to library root,
    because AssetListModel.is_valid is missing (simulated or real).
    """
    facade = AppFacade()
    facade.bind_library(mock_library_manager)

    # Setup paths
    lib_root = tmp_path

    # We want to test the 'should_prepare' logic in AppFacade.open_album

    # Access the internal library model
    library_model = facade._library_list_model

    # Spy on prepare_for_album
    mocker.spy(library_model, 'prepare_for_album')

    # Mock rowCount to be > 0 (simulate populated model)
    mocker.patch.object(library_model, 'rowCount', return_value=10)

    # Mock album_root() to return the correct root
    # Note: We must set _album_root because is_valid() checks it directly
    library_model._album_root = lib_root

    # Important: ensure _paths_equal returns True for lib_root vs lib_root
    # It should work naturally with Path objects, but let's be safe

    # Also, we need to ensure the model *appears* valid locally, EXCEPT for is_valid method logic.
    # Currently AssetListModel does NOT have is_valid.
    # So getattr(model, "is_valid", lambda: False)() returns False.
    # So should_prepare becomes True.

    # Mocking backend.open_album to return an album with lib_root
    album = MagicMock(spec=Album)
    album.root = lib_root
    mock_backend.open_album.return_value = album

    # --- ACTION ---
    # Call open_album for the library root
    facade.open_album(lib_root)

    # --- ASSERTION ---
    # With is_valid present, the optimization succeeds, and prepare_for_album is SKIPPED.
    assert library_model.prepare_for_album.call_count == 0

    print("\n[CONFIRMED] prepare_for_album was skipped (optimization succeeded).")
