"""Tests for VideoArea widget."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimediaWidgets", reason="QtMultimediaWidgets is required")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QShowEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from iPhoto.config import VIDEO_COMPLETE_HOLD_BACKSTEP_MS
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


def test_set_surface_color_updates_background(qapp):
    """set_surface_color should update the scene background and default colour."""
    video_area = VideoArea()

    video_area.set_surface_color("#000000")
    assert video_area._default_surface_color == "#000000"
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")


def test_set_surface_color_light_mode(qapp):
    """In light mode the surface should match the provided colour."""
    video_area = VideoArea()

    video_area.set_surface_color("#f0f0f0")
    assert video_area._default_surface_color == "#f0f0f0"
    assert video_area._scene.backgroundBrush().color() == QColor("#f0f0f0")


def test_immersive_restores_default_surface(qapp):
    """set_immersive_background(False) should restore the default surface colour."""
    video_area = VideoArea()
    video_area.set_surface_color("#abcdef")

    video_area.set_immersive_background(True)
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")

    video_area.set_immersive_background(False)
    assert video_area._scene.backgroundBrush().color() == QColor("#abcdef")


def test_end_of_media_backsteps_and_pauses(qapp, mocker):
    """When EndOfMedia fires, the player should backstep and pause."""
    video_area = VideoArea()

    mocker.patch.object(video_area._player, "duration", return_value=5000)
    mocker.patch.object(video_area._player, "position", return_value=5000)
    mock_set_pos = mocker.patch.object(video_area._player, "setPosition")
    mock_pause = mocker.patch.object(video_area._player, "pause")

    video_area._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)

    mock_set_pos.assert_called_once_with(5000 - VIDEO_COMPLETE_HOLD_BACKSTEP_MS)
    mock_pause.assert_called_once()
