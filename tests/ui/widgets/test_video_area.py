"""Tests for VideoArea widget."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimediaWidgets", reason="QtMultimediaWidgets is required")

from PySide6.QtCore import QEvent, QPointF, QRectF, QSizeF, Qt
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


# ------------------------------------------------------------------
# Black backing geometry
# ------------------------------------------------------------------
def test_backing_collapsed_when_no_video(qapp, mocker):
    """Black backing should be collapsed when no video is loaded."""
    video_area = VideoArea()

    # nativeSize() returns empty when no video is loaded
    mocker.patch.object(video_area._video_item, "nativeSize", return_value=QSizeF())

    video_area._update_black_backing_geometry()
    assert video_area._black_backing.rect() == QRectF()


def test_backing_tracks_video_frame(qapp, mocker):
    """Black backing should match the rendered video frame area."""
    video_area = VideoArea()

    # Simulate a 1920x1080 video in a 800x600 item
    mocker.patch.object(video_area._video_item, "nativeSize", return_value=QSizeF(1920, 1080))
    mocker.patch.object(video_area._video_item, "size", return_value=QSizeF(800, 600))
    mocker.patch.object(video_area._video_item, "pos", return_value=QPointF(0, 0))

    video_area._update_black_backing_geometry()

    backing_rect = video_area._black_backing.rect()
    # 1920x1080 scaled to fit 800x600 → 800x450 centred
    assert abs(backing_rect.width() - 800.0) < 0.01
    assert abs(backing_rect.height() - 450.0) < 0.01
    # Centred vertically: (600-450)/2 = 75
    assert abs(backing_rect.y() - 75.0) < 0.01


def test_backing_letterbox_for_tall_video(qapp, mocker):
    """Pillarboxed video should have black backing narrower than item."""
    video_area = VideoArea()

    # Simulate a 1080x1920 (portrait) video in a 800x600 item
    mocker.patch.object(video_area._video_item, "nativeSize", return_value=QSizeF(1080, 1920))
    mocker.patch.object(video_area._video_item, "size", return_value=QSizeF(800, 600))
    mocker.patch.object(video_area._video_item, "pos", return_value=QPointF(0, 0))

    video_area._update_black_backing_geometry()

    backing_rect = video_area._black_backing.rect()
    # 1080x1920 scaled to fit 800x600 → 337.5x600 centred
    assert abs(backing_rect.height() - 600.0) < 0.01
    assert backing_rect.width() < 800.0  # pillarboxed
    # Centred horizontally
    expected_w = 600.0 * 1080.0 / 1920.0  # 337.5
    assert abs(backing_rect.width() - expected_w) < 0.01


def test_surface_color_does_not_affect_backing(qapp):
    """Changing surface colour should not affect the black backing."""
    video_area = VideoArea()

    video_area.set_surface_color("#f0f0f0")
    # Scene background follows theme, backing stays black
    assert video_area._scene.backgroundBrush().color() == QColor("#f0f0f0")
    assert video_area._black_backing.brush().color() == QColor(Qt.GlobalColor.black)
