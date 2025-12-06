"""Unit tests for EditController logic."""

from __future__ import annotations

import os
from unittest.mock import Mock, patch
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtCore import QObject, Signal

from src.iPhoto.gui.ui.controllers.edit_controller import EditController
from src.iPhoto.gui.ui.models.edit_session import EditSession

@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

class StubSidebar(QObject):
    interactionStarted = Signal()
    bwParamsPreviewed = Signal(object)
    bwParamsCommitted = Signal(object)
    perspectiveInteractionStarted = Signal()
    perspectiveInteractionFinished = Signal()

    def __init__(self):
        super().__init__()
        self.preview_thumbnail_height = Mock(return_value=100)
        self.set_session = Mock()
        self.set_mode = Mock()
        self.set_light_preview_image = Mock()
        self.refresh = Mock()

class StubViewer(QObject):
    cropInteractionStarted = Signal()
    fullscreenExitRequested = Signal()
    zoomChanged = Signal(float)
    cropChanged = Signal(float, float, float, float)

    def __init__(self):
        super().__init__()
        self.setCropMode = Mock()
        self.set_zoom = Mock()
        self.reset_zoom = Mock()
        self.set_image = Mock()
        self.set_adjustments = Mock()
        self.current_image_source = Mock(return_value=None)
        self.pixmap = Mock()
        self.set_loading = Mock()
        self.viewport_center = Mock()
        self.start_perspective_interaction = Mock()
        self.end_perspective_interaction = Mock()

class StubUi(QObject):
    def __init__(self):
        super().__init__()
        self.edit_sidebar = StubSidebar()
        self.edit_image_viewer = StubViewer()
        self.edit_reset_button = Mock()
        self.edit_done_button = Mock()
        self.edit_rotate_left_button = Mock()
        self.edit_adjust_action = Mock()
        self.edit_crop_action = Mock()
        self.edit_compare_button = Mock()
        self.edit_mode_control = Mock()
        self.edit_header_container = Mock()
        self.zoom_in_button = Mock()
        self.zoom_out_button = Mock()
        self.zoom_slider = Mock()
        self.zoom_widget = Mock()
        self.info_button = Mock()
        self.favorite_button = Mock()
        self.edit_right_controls_layout = Mock()
        self.edit_zoom_host_layout = Mock()
        self.detail_actions_layout = Mock()
        self.detail_header_layout = Mock()

        # Add missing attributes for HeaderLayoutManager if needed
        self.detail_info_button_index = 0
        self.detail_favorite_button_index = 1
        self.detail_zoom_widget_index = 2

@pytest.fixture
def edit_controller_setup(qapp):
    ui = StubUi()
    view_controller = Mock()
    view_controller.is_edit_view_active.return_value = True
    player_view = Mock()
    playlist = Mock()
    playlist.current_source.return_value = None
    asset_model = Mock()

    # Patch dependencies that are created inside __init__
    with patch("src.iPhoto.gui.ui.controllers.edit_controller.EditPipelineLoader") as MockPipelineLoader, \
         patch("src.iPhoto.gui.ui.controllers.edit_controller.EditPreviewManager") as MockPreviewManager, \
         patch("src.iPhoto.gui.ui.controllers.edit_controller.EditViewTransitionManager"), \
         patch("src.iPhoto.gui.ui.controllers.edit_controller.EditFullscreenManager"):

        controller = EditController(
            ui=ui,
            view_controller=view_controller,
            player_view=player_view,
            playlist=playlist,
            asset_model=asset_model
        )

        # Inject a session
        session = EditSession()
        controller._session = session
        controller._history_manager.set_session(session)
        controller._current_source = Path("/path/to/image.jpg")

        return controller, MockPipelineLoader.return_value, MockPreviewManager.return_value

def test_on_edit_image_loaded_computes_target_height(edit_controller_setup):
    """Verify that _on_edit_image_loaded computes target_height and calls prepare_sidebar_preview."""
    controller, mock_pipeline_loader, mock_preview_manager = edit_controller_setup

    path = Path("/path/to/image.jpg")
    image = QImage(1000, 1000, QImage.Format.Format_RGB32)

    # Setup mocks
    mock_preview_manager.generate_scaled_neutral_preview.return_value = image # Return self for simplicity

    # Configure sidebar height
    # track_height = 100 -> target_height = max(100 * 6, 320) = 600
    controller._ui.edit_sidebar.preview_thumbnail_height.return_value = 100

    # Trigger the slot
    controller._on_edit_image_loaded(path, image)

    # Verify generate_scaled_neutral_preview called
    mock_preview_manager.generate_scaled_neutral_preview.assert_called()

    # Verify prepare_sidebar_preview called with CORRECT target_height
    mock_pipeline_loader.prepare_sidebar_preview.assert_called_once()
    _, kwargs = mock_pipeline_loader.prepare_sidebar_preview.call_args

    # Expected: max(100 * 6, 320) = 600
    assert kwargs['target_height'] == 600
    assert kwargs['full_res_image_for_fallback'] == image

def test_on_edit_image_loaded_computes_target_height_min_clamp(edit_controller_setup):
    """Verify minimum clamping logic for target_height."""
    controller, mock_pipeline_loader, mock_preview_manager = edit_controller_setup

    path = Path("/path/to/image.jpg")
    image = QImage(1000, 1000, QImage.Format.Format_RGB32)

    mock_preview_manager.generate_scaled_neutral_preview.return_value = image

    # Configure sidebar height to be small
    # track_height = 10 -> 10 * 6 = 60. max(60, 320) = 320.
    controller._ui.edit_sidebar.preview_thumbnail_height.return_value = 10

    controller._on_edit_image_loaded(path, image)

    _, kwargs = mock_pipeline_loader.prepare_sidebar_preview.call_args
    assert kwargs['target_height'] == 320
