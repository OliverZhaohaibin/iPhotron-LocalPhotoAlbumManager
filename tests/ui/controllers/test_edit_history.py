"""Unit tests for edit history (undo/redo) logic in EditController and EditHistoryManager."""

from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for edit controller tests",
    exc_type=ImportError,
)

from PySide6.QtCore import QObject, Signal, QSize
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette

from src.iPhoto.gui.ui.controllers.edit_controller import EditController
from src.iPhoto.gui.ui.models.edit_session import EditSession


@pytest.fixture()
def qapp() -> QApplication:
    """Provide a QApplication for Qt widgets."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class StubSidebar(QObject):
    """Stub for EditSidebar exposing interaction signals."""

    interactionStarted = Signal()
    interactionFinished = Signal()
    bwParamsCommitted = Signal(object)
    bwParamsPreviewed = Signal(object)
    perspectiveInteractionStarted = Signal()
    perspectiveInteractionFinished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.preview_thumbnail_height = Mock(return_value=100)
        self.set_session = Mock()
        self.set_mode = Mock()
        self.set_light_preview_image = Mock()
        self.refresh = Mock()
        self.sizeHint = Mock(return_value=QSize(100, 100))
        self.property = Mock(return_value=None)
        self.minimumWidth = Mock(return_value=100)
        self.maximumWidth = Mock(return_value=200)

class StubViewer(QObject):
    """Stub for GLImageViewer exposing crop signals."""

    cropInteractionStarted = Signal()
    cropInteractionFinished = Signal()
    cropChanged = Signal(float, float, float, float)
    fullscreenExitRequested = Signal()
    zoomChanged = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setCropMode = Mock()
        self.set_zoom = Mock()
        self.reset_zoom = Mock()
        self.set_image = Mock()
        self.set_adjustments = Mock()
        self.current_image_source = Mock(return_value=None)
        self.pixmap = Mock()
        self.set_loading = Mock()
        self.crop_values = Mock(return_value={
            "Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 1.0, "Crop_H": 1.0
        })
        self.rotate_image_ccw = Mock(return_value={})
        self.viewport_center = Mock()
        self.start_perspective_interaction = Mock()
        self.end_perspective_interaction = Mock()


class StubUi(QObject):
    """Stub for Ui_MainWindow."""

    def __init__(self) -> None:
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

        # Zoom controls
        self.zoom_in_button = Mock()
        self.zoom_out_button = Mock()
        self.zoom_slider = Mock()
        self.zoom_widget = Mock()

        # Header shared controls
        self.info_button = Mock()
        self.favorite_button = Mock()
        self.edit_right_controls_layout = Mock()
        self.edit_zoom_host_layout = Mock()
        self.detail_actions_layout = Mock()
        self.detail_header_layout = Mock()

        # Detail page for theme manager
        self.detail_page = Mock()
        self.detail_page.edit_container = Mock()
        self.sidebar = Mock()
        self.status_bar = Mock()
        self.window_chrome = Mock()
        self.window_shell = Mock()
        self.title_bar = Mock()
        self.title_separator = Mock()
        self.menu_bar_container = Mock()
        self.menu_bar = Mock()
        self.rescan_button = Mock()
        self.selection_button = Mock()
        self.window_title_label = Mock()

        # Configure palettes
        dummy_palette = QPalette()
        for widget in [
            self.sidebar, self.status_bar, self.window_chrome, self.window_shell,
            self.title_bar, self.title_separator, self.menu_bar_container,
            self.menu_bar, self.rescan_button, self.selection_button,
            self.window_title_label
        ]:
            widget.palette.return_value = dummy_palette

        # Nested widgets
        self.sidebar._tree = Mock()
        self.sidebar._tree.palette.return_value = dummy_palette
        self.status_bar._message_label = Mock()
        self.status_bar._message_label.palette.return_value = dummy_palette


@pytest.fixture
def edit_controller(qapp):
    """Fixture to create an EditController with mocked dependencies."""
    ui = StubUi()
    view_controller = Mock()
    view_controller.is_edit_view_active.return_value = True
    player_view = Mock()
    playlist = Mock()
    playlist.current_source.return_value = None
    asset_model = Mock()

    with patch("src.iPhoto.gui.ui.controllers.edit_controller.EditViewTransitionManager") as MockTransitionManager, \
         patch("src.iPhoto.gui.ui.controllers.edit_controller.EditFullscreenManager") as MockFullscreenManager, \
         patch("src.iPhoto.gui.ui.controllers.edit_controller.EditPreviewManager") as MockPreviewManager:

        # We use the REAL EditController but mock its inputs
        controller = EditController(
            ui=ui,
            view_controller=view_controller,
            player_view=player_view,
            playlist=playlist,
            asset_model=asset_model
        )

        # Manually initialize session for testing
        session = EditSession()
        controller._session = session
        controller._history_manager.set_session(session)

        return controller


def test_undo_stack_initialization(edit_controller):
    """Verify undo stacks are initialized empty."""
    # Now accessed via _history_manager
    manager = edit_controller._history_manager
    assert hasattr(manager, "_undo_stack")
    assert manager._undo_stack == []
    assert hasattr(manager, "_redo_stack")
    assert manager._redo_stack == []


def test_push_undo_state(edit_controller):
    """Verify pushing state saves current values and clears redo."""
    session = edit_controller._session
    session.set_value("Light_Master", 0.5)

    # Simulate action start
    edit_controller.push_undo_state()

    manager = edit_controller._history_manager
    assert len(manager._undo_stack) == 1
    saved_state = manager._undo_stack[0]
    assert saved_state["Light_Master"] == 0.5

    # Change value
    session.set_value("Light_Master", 0.8)

    # Undo
    edit_controller.undo()
    assert session.value("Light_Master") == 0.5
    assert len(manager._undo_stack) == 0
    assert len(manager._redo_stack) == 1


def test_redo_logic(edit_controller):
    """Verify redo restores the future state."""
    session = edit_controller._session
    session.set_value("Light_Master", 0.0)

    # 1. Push state (0.0)
    edit_controller.push_undo_state()
    # 2. Change to 0.5
    session.set_value("Light_Master", 0.5)

    # Undo -> Should go back to 0.0
    edit_controller.undo()
    assert session.value("Light_Master") == 0.0

    # Redo -> Should go forward to 0.5
    edit_controller.redo()
    assert session.value("Light_Master") == 0.5


def test_history_limit(edit_controller):
    """Verify history stack respects the limit."""
    limit = 50
    session = edit_controller._session
    manager = edit_controller._history_manager

    for i in range(limit + 10):
        # Use values within [-1.0, 1.0] range to avoid clamping
        val = float(i) / 100.0
        session.set_value("Light_Master", val)
        edit_controller.push_undo_state()

    assert len(manager._undo_stack) == limit
    # The oldest states (0..9) should have been dropped.
    # The stack should contain 10..59 (values 0.10 .. 0.59)
    assert abs(manager._undo_stack[0]["Light_Master"] - 0.10) < 1e-6


def test_signal_connections(edit_controller, qapp):
    """Verify widget signals trigger push_undo_state."""
    # Mock push_undo_state to verify call
    edit_controller.push_undo_state = Mock()

    # Emit sidebar interaction signal
    edit_controller._ui.edit_sidebar.interactionStarted.emit()
    edit_controller.push_undo_state.assert_called_once()

    edit_controller.push_undo_state.reset_mock()

    # Emit viewer crop interaction signal
    edit_controller._ui.edit_image_viewer.cropInteractionStarted.emit()
    edit_controller.push_undo_state.assert_called_once()


def test_undo_with_empty_stack_does_nothing(edit_controller):
    """Verify undo handles empty stack gracefully."""
    session = edit_controller._session
    session.set_value("Light_Master", 0.5)

    edit_controller.undo()

    # Value should remain unchanged
    assert session.value("Light_Master") == 0.5
