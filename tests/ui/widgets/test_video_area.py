"""Tests for VideoArea widget."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")

from PySide6.QtCore import QEvent, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QShowEvent
from PySide6.QtWidgets import QApplication, QGraphicsRectItem

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
# Black backing & scene background tests
# ------------------------------------------------------------------


def test_black_backing_exists_behind_video_item(qapp):
    """The black backing rectangle must sit behind the video item (Z < video)."""
    va = VideoArea()
    assert isinstance(va._black_backing, QGraphicsRectItem)
    assert va._black_backing.zValue() < va._video_item.zValue()


def test_black_backing_is_black(qapp):
    """The backing must be filled with solid black for HDR/HEVC compositing."""
    va = VideoArea()
    assert va._black_backing.brush().color() == QColor(Qt.GlobalColor.black)


def test_scene_background_matches_surface_color(qapp):
    """The scene background should use the surface colour, not always black."""
    va = VideoArea()
    # After construction, scene brush should NOT be pure black
    # (it follows the palette-driven surface colour).
    scene_brush_color = va._scene.backgroundBrush().color()
    # The exact surface colour depends on the platform palette, but it
    # should match _default_surface_color.
    assert scene_brush_color == QColor(va._default_surface_color)


def test_apply_surface_color_updates_scene_background(qapp):
    """_apply_surface_color should set the scene background to the given colour."""
    va = VideoArea()
    va._apply_surface_color("#FF0000")
    assert va._scene.backgroundBrush().color() == QColor("#FF0000")


def test_black_backing_collapsed_when_no_video_loaded(qapp):
    """Without a loaded video the backing should be collapsed (empty rect)."""
    va = VideoArea()
    va._update_black_backing_geometry()
    assert va._black_backing.rect().isEmpty()


def test_set_surface_color_override_updates_scene(qapp):
    """set_surface_color_override should update scene background to the given colour."""
    va = VideoArea()
    va.set_surface_color_override("#123456")
    assert va._scene.backgroundBrush().color() == QColor("#123456")
