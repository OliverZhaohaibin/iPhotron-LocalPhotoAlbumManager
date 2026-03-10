"""Tests for VideoArea widget."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimediaWidgets", reason="QtMultimediaWidgets is required")

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
    video_area = VideoArea()
    mock_update = mocker.patch.object(video_area, '_update_bar_geometry')
    show_event = QShowEvent()
    video_area.showEvent(show_event)
    mock_update.assert_called_once()


def test_video_area_show_event_calls_super(qapp, mocker):
    """Test that showEvent calls the parent class's showEvent."""
    video_area = VideoArea()
    mock_super_show = mocker.patch('PySide6.QtWidgets.QWidget.showEvent')
    show_event = QShowEvent()
    video_area.showEvent(show_event)
    mock_super_show.assert_called_once_with(show_event)


def test_set_surface_color_stores_default(qapp):
    """set_surface_color should store the new default colour."""
    video_area = VideoArea()

    video_area.set_surface_color("#f0f0f0")
    assert video_area._default_surface_color == "#f0f0f0"


def test_scene_background_always_black(qapp):
    """Scene background must always be black for correct HDR/HEVC compositing."""
    video_area = VideoArea()

    # Initially black
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")

    # Stays black even after setting a light surface colour
    video_area.set_surface_color("#f0f0f0")
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")

    # Stays black after immersive toggle
    video_area.set_immersive_background(True)
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")

    video_area.set_immersive_background(False)
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")


def test_immersive_restores_default_surface(qapp):
    """set_immersive_background(False) should restore the default surface colour."""
    video_area = VideoArea()
    video_area.set_surface_color("#abcdef")

    video_area.set_immersive_background(True)
    # Scene is always black
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")

    video_area.set_immersive_background(False)
    # Scene stays black; default_surface_color is restored for chrome
    assert video_area._default_surface_color == "#abcdef"
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")


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


def test_scene_has_no_black_backing(qapp):
    """The scene should contain only the video item, no black backing rectangle."""
    video_area = VideoArea()

    # Only one item in the scene: the video item itself
    items = video_area._scene.items()
    assert len(items) == 1
    assert items[0] is video_area._video_item
