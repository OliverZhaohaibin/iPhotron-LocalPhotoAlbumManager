"""Tests for VideoArea widget."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QShowEvent
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.video_area import VideoArea


@pytest.fixture
def qapp():
    """Create QApplication instance for Qt tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_video_area_show_event_calls_update_bar_geometry(qapp, mocker):
    """Test that showEvent calls _update_bar_geometry to fix initial position."""
    # Create a VideoArea widget
    video_area = VideoArea()
    
    # Mock the _update_bar_geometry method to track if it's called
    mock_update = mocker.patch.object(video_area, '_update_bar_geometry')
    
    # Create a show event
    show_event = QShowEvent()
    
    # Call showEvent
    video_area.showEvent(show_event)
    
    # Verify that _update_bar_geometry was called
    mock_update.assert_called_once()


def test_video_area_show_event_calls_super(qapp, mocker):
    """Test that showEvent calls the parent class's showEvent."""
    # Create a VideoArea widget
    video_area = VideoArea()
    
    # Mock the parent showEvent
    mock_super_show = mocker.patch('PySide6.QtWidgets.QWidget.showEvent')
    
    # Create a show event
    show_event = QShowEvent()
    
    # Call showEvent
    video_area.showEvent(show_event)
    
    # Verify that super().showEvent was called with the event
    mock_super_show.assert_called_once_with(show_event)


# ------------------------------------------------------------------
# Scene background tests
# ------------------------------------------------------------------


def test_scene_background_is_always_black(qapp):
    """Scene background must always be black for correct HDR/HEVC compositing."""
    va = VideoArea()
    assert va._scene.backgroundBrush().color() == QColor("#000000")


def test_apply_surface_color_does_not_change_scene(qapp):
    """_apply_surface_color must not alter the scene background (always black)."""
    va = VideoArea()
    va._apply_surface_color("#FF0000")
    assert va._scene.backgroundBrush().color() == QColor("#000000")


def test_set_surface_color_override_keeps_scene_black(qapp):
    """set_surface_color_override updates widget chrome but scene stays black."""
    va = VideoArea()
    va.set_surface_color_override("#123456")
    assert va._scene.backgroundBrush().color() == QColor("#000000")
    assert va._default_surface_color == "#123456"


def test_immersive_background_keeps_scene_black(qapp):
    """set_immersive_background must not change the always-black scene."""
    va = VideoArea()
    va.set_immersive_background(True)
    assert va._scene.backgroundBrush().color() == QColor("#000000")
    va.set_immersive_background(False)
    assert va._scene.backgroundBrush().color() == QColor("#000000")
