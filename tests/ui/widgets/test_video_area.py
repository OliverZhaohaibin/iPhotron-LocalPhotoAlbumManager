"""Tests for VideoArea widget."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimediaWidgets", reason="QtMultimediaWidgets is required")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QShowEvent
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrameFormat
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
# HDR detection
# ------------------------------------------------------------------
def _make_mock_frame(*, transfer, color_space):
    """Build a mock QVideoFrame with the given colour metadata."""
    fmt = MagicMock()
    fmt.colorTransfer.return_value = transfer
    fmt.colorSpace.return_value = color_space
    frame = MagicMock()
    frame.isValid.return_value = True
    frame.surfaceFormat.return_value = fmt
    return frame


def test_hdr_hlg_forces_black_scene(qapp):
    """HLG (arib-std-b67) video should force the scene to black."""
    video_area = VideoArea()
    video_area.set_surface_color("#f0f0f0")

    frame = _make_mock_frame(
        transfer=QVideoFrameFormat.ColorTransfer.ColorTransfer_STD_B67,
        color_space=QVideoFrameFormat.ColorSpace.ColorSpace_BT2020,
    )
    video_area._detect_hdr_once(frame)

    assert video_area._hdr_detected is True
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")


def test_hdr_pq_forces_black_scene(qapp):
    """PQ / HDR-10 video should force the scene to black."""
    video_area = VideoArea()
    video_area.set_surface_color("#f0f0f0")

    frame = _make_mock_frame(
        transfer=QVideoFrameFormat.ColorTransfer.ColorTransfer_ST2084,
        color_space=QVideoFrameFormat.ColorSpace.ColorSpace_BT2020,
    )
    video_area._detect_hdr_once(frame)

    assert video_area._hdr_detected is True
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")


def test_bt2020_without_hdr_transfer_detected(qapp):
    """BT.2020 colour space alone is treated as wide-gamut and forces black."""
    video_area = VideoArea()
    video_area.set_surface_color("#f0f0f0")

    frame = _make_mock_frame(
        transfer=QVideoFrameFormat.ColorTransfer.ColorTransfer_BT709,
        color_space=QVideoFrameFormat.ColorSpace.ColorSpace_BT2020,
    )
    video_area._detect_hdr_once(frame)

    assert video_area._hdr_detected is True
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")


def test_sdr_keeps_surface_color(qapp):
    """Standard BT.709 SDR video should keep the themed surface colour."""
    video_area = VideoArea()
    video_area.set_surface_color("#f0f0f0")

    frame = _make_mock_frame(
        transfer=QVideoFrameFormat.ColorTransfer.ColorTransfer_BT709,
        color_space=QVideoFrameFormat.ColorSpace.ColorSpace_BT709,
    )
    video_area._detect_hdr_once(frame)

    assert video_area._hdr_detected is False
    assert video_area._scene.backgroundBrush().color() == QColor("#f0f0f0")


def test_detect_hdr_only_runs_once(qapp):
    """Subsequent frames must not re-trigger the HDR check."""
    video_area = VideoArea()
    video_area.set_surface_color("#f0f0f0")

    sdr_frame = _make_mock_frame(
        transfer=QVideoFrameFormat.ColorTransfer.ColorTransfer_BT709,
        color_space=QVideoFrameFormat.ColorSpace.ColorSpace_BT709,
    )
    hdr_frame = _make_mock_frame(
        transfer=QVideoFrameFormat.ColorTransfer.ColorTransfer_STD_B67,
        color_space=QVideoFrameFormat.ColorSpace.ColorSpace_BT2020,
    )

    video_area._detect_hdr_once(sdr_frame)
    assert video_area._hdr_detected is False

    # Second call should be a no-op
    video_area._detect_hdr_once(hdr_frame)
    assert video_area._hdr_detected is False


def test_load_video_resets_hdr_state(qapp, mocker):
    """Loading a new video should reset HDR detection."""
    video_area = VideoArea()
    video_area._hdr_detected = True
    video_area._hdr_checked = True
    video_area.set_surface_color("#f0f0f0")

    mocker.patch.object(video_area._player, "setSource")
    mocker.patch.object(video_area._player, "setPosition")

    video_area.load_video(Path("/tmp/test.mp4"))

    assert video_area._hdr_detected is False
    assert video_area._hdr_checked is False
    assert video_area._scene.backgroundBrush().color() == QColor("#f0f0f0")


def test_set_surface_color_respects_hdr(qapp):
    """Changing surface colour while HDR is active keeps the scene black."""
    video_area = VideoArea()
    video_area._hdr_detected = True

    video_area.set_surface_color("#abcdef")

    # Widget chrome follows the theme colour, but scene stays black
    assert video_area._default_surface_color == "#abcdef"
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")


def test_immersive_exit_respects_hdr(qapp):
    """Leaving immersive mode with HDR active keeps the scene black."""
    video_area = VideoArea()
    video_area._hdr_detected = True
    video_area.set_surface_color("#abcdef")

    video_area.set_immersive_background(True)
    video_area.set_immersive_background(False)

    assert video_area._scene.backgroundBrush().color() == QColor("#000000")
