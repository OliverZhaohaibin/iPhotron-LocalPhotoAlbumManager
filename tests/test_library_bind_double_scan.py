
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from src.iPhoto.library.manager import LibraryManager

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_watcher_active_during_init_deleted_dir(tmp_path, qapp):
    """
    Verify that the file system watcher is active during _initialize_deleted_dir
    when re-binding a library, which causes the double-scan issue.
    """
    root = tmp_path / "Library"
    root.mkdir()

    manager = LibraryManager()
    manager.bind_path(root)

    # After first bind, the root should be watched
    assert str(root) in manager._watcher.directories()

    # We want to verify that when we call bind_path AGAIN,
    # the watcher is still active when _initialize_deleted_dir is called.

    original_init = manager._initialize_deleted_dir

    watcher_was_active = False

    def side_effect():
        nonlocal watcher_was_active
        # Check if we are watching anything
        if manager._watcher.directories():
            watcher_was_active = True
        original_init()

    with patch.object(manager, '_initialize_deleted_dir', side_effect=side_effect):
        manager.bind_path(root)

    # WITHOUT THE FIX: assertion should be True (watcher was active)
    # WITH THE FIX: assertion should be False (watcher was cleared)
    assert watcher_was_active is False, "Watcher should NOT be active during init (bug fixed)"
