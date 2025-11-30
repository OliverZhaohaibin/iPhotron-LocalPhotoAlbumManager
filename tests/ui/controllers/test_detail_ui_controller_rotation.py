"""Test the rotation logic in DetailUIController."""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
# Set offscreen platform before importing QtWidgets
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QPushButton, QSlider, QToolButton, QWidget

# Mock the modules that might cause issues or are heavy dependencies
sys.modules["iPhotos.src.iPhoto.gui.icons"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.widgets.asset_grid"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.widgets.info_panel"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.widgets.player_bar"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.widgets.gl_image_viewer"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.ui_main_window"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.ui.controllers.header_controller"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.ui.controllers.player_view_controller"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.ui.controllers.view_controller"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.gui.ui.controllers.navigation_controller"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.io.sidecar"] = MagicMock()
sys.modules["iPhotos.src.iPhoto.io.metadata"] = MagicMock()

# Import the controller under test
from iPhotos.src.iPhoto.gui.ui.controllers.detail_ui_controller import DetailUIController
from iPhotos.src.iPhoto.gui.ui.models.asset_model import Roles

@pytest.fixture
def qapp():
    """Return the shared QApplication instance for the test suite."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

@pytest.fixture
def mock_dependencies(qapp):
    """Create mocks for all dependencies of DetailUIController."""
    mocks = {
        "model": MagicMock(),
        "filmstrip_view": MagicMock(),
        "player_view": MagicMock(),
        "player_bar": MagicMock(),
        "view_controller": MagicMock(),
        "header": MagicMock(),
        "favorite_button": QToolButton(),
        "rotate_left_button": QToolButton(),
        "edit_button": QPushButton(),
        "info_button": QToolButton(),
        "info_panel": MagicMock(),
        "zoom_widget": QWidget(),
        "zoom_slider": QSlider(Qt.Orientation.Horizontal),
        "zoom_in_button": QToolButton(),
        "zoom_out_button": QToolButton(),
        "status_bar": MagicMock(),
        "navigation_controller": MagicMock(),
    }

    # Setup model mock behavior for index
    def get_index(row, column):
        index = MagicMock(spec=QModelIndex)
        index.isValid.return_value = True
        index.row.return_value = row
        index.column.return_value = column
        # Setup data return values
        def data(role):
            if role == Roles.ABS:
                return "/tmp/test/image.jpg"
            if role == Roles.REL:
                return "image.jpg"
            if role == Roles.IS_IMAGE:
                return True
            return None
        index.data.side_effect = data
        return index

    mocks["model"].index.side_effect = get_index

    # Setup player view image viewer mock
    image_viewer = MagicMock()
    image_viewer.rotate_image_ccw.return_value = {"orientation": 6}
    mocks["player_view"].image_viewer = image_viewer

    return mocks

def test_rotate_left_suppresses_tree_refresh(mock_dependencies):
    """Verify that rotating an image suppresses the tree refresh."""

    # Initialize controller
    controller = DetailUIController(
        model=mock_dependencies["model"],
        filmstrip_view=mock_dependencies["filmstrip_view"],
        player_view=mock_dependencies["player_view"],
        player_bar=mock_dependencies["player_bar"],
        view_controller=mock_dependencies["view_controller"],
        header=mock_dependencies["header"],
        favorite_button=mock_dependencies["favorite_button"],
        rotate_left_button=mock_dependencies["rotate_left_button"],
        edit_button=mock_dependencies["edit_button"],
        info_button=mock_dependencies["info_button"],
        info_panel=mock_dependencies["info_panel"],
        zoom_widget=mock_dependencies["zoom_widget"],
        zoom_slider=mock_dependencies["zoom_slider"],
        zoom_in_button=mock_dependencies["zoom_in_button"],
        zoom_out_button=mock_dependencies["zoom_out_button"],
        status_bar=mock_dependencies["status_bar"],
        navigation_controller=mock_dependencies["navigation_controller"],
    )

    # Simulate selecting an image (row 0)
    controller.handle_playlist_current_changed(0, -1)

    # Mock sidecar load/save to prevent actual IO
    with patch("iPhotos.src.iPhoto.gui.ui.controllers.detail_ui_controller.sidecar") as mock_sidecar:
        mock_sidecar.load_adjustments.return_value = {}

        # Trigger rotation
        controller._handle_rotate_left_clicked()

        # Verify that save_adjustments was called
        mock_sidecar.save_adjustments.assert_called_once()

        # Verify expected interaction with navigation controller
        # This is the key assertion for the fix
        mock_dependencies["navigation_controller"].suppress_tree_refresh_for_edit.assert_called_once()
        mock_dependencies["navigation_controller"].suspend_library_watcher.assert_not_called()
