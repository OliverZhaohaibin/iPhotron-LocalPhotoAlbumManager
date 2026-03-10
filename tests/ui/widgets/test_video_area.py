"""Tests for VideoArea widget."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimediaWidgets", reason="QtMultimediaWidgets is required")

from unittest.mock import MagicMock
from pathlib import Path

from PySide6.QtCore import QSizeF
from PySide6.QtGui import QColor, QImage, QShowEvent
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoFrameFormat
from PySide6.QtWidgets import QApplication

from iPhoto.config import VIDEO_COMPLETE_HOLD_BACKSTEP_MS
from iPhoto.gui.ui.widgets.video_area import (
    VideoArea,
    _SdrVideoItem,
    _is_hdr_frame_format,
)


@pytest.fixture
def qapp():
    """Create QApplication instance for Qt tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ------------------------------------------------------------------
# HDR detection helper
# ------------------------------------------------------------------

def test_is_hdr_bt2020(qapp):
    """BT.2020 colour space should be detected as HDR."""
    fmt = QVideoFrameFormat()
    fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT2020)
    assert _is_hdr_frame_format(fmt) is True


def test_is_hdr_hlg(qapp):
    """STD-B67 (HLG) transfer function should be detected as HDR."""
    fmt = QVideoFrameFormat()
    fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_STD_B67)
    assert _is_hdr_frame_format(fmt) is True


def test_is_hdr_pq(qapp):
    """ST 2084 (PQ / HDR-10) transfer function should be detected as HDR."""
    fmt = QVideoFrameFormat()
    fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_ST2084)
    assert _is_hdr_frame_format(fmt) is True


def test_is_sdr_bt709(qapp):
    """BT.709 content should NOT be detected as HDR."""
    fmt = QVideoFrameFormat()
    fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT709)
    fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_BT709)
    assert _is_hdr_frame_format(fmt) is False


def test_is_sdr_undefined(qapp):
    """Undefined colour space should NOT be detected as HDR."""
    fmt = QVideoFrameFormat()
    assert _is_hdr_frame_format(fmt) is False


# ------------------------------------------------------------------
# _SdrVideoItem
# ------------------------------------------------------------------

def test_sdr_item_initial_state(qapp):
    """_SdrVideoItem starts with an empty image and zero bounding rect."""
    item = _SdrVideoItem()
    assert item.boundingRect().isEmpty()
    assert item._image.isNull()


def test_sdr_item_set_size(qapp):
    """setSize should update the bounding rect."""
    item = _SdrVideoItem()
    item.setSize(QSizeF(640, 480))
    assert item.boundingRect().width() == 640
    assert item.boundingRect().height() == 480


def test_sdr_item_clear_frame(qapp):
    """clearFrame should reset the stored image."""
    item = _SdrVideoItem()
    item._image = QImage(100, 100, QImage.Format.Format_ARGB32)
    assert not item._image.isNull()
    item.clearFrame()
    assert item._image.isNull()


# ------------------------------------------------------------------
# VideoArea – scene background follows theme
# ------------------------------------------------------------------

def test_scene_background_follows_theme(qapp):
    """Scene background should follow the theme colour, NOT forced black."""
    video_area = VideoArea()

    video_area.set_surface_color("#f0f0f0")
    assert video_area._scene.backgroundBrush().color() == QColor("#f0f0f0")

    video_area.set_surface_color("#abcdef")
    assert video_area._scene.backgroundBrush().color() == QColor("#abcdef")


def test_immersive_restores_theme_surface(qapp):
    """set_immersive_background(False) should restore the theme colour."""
    video_area = VideoArea()
    video_area.set_surface_color("#abcdef")

    video_area.set_immersive_background(True)
    assert video_area._scene.backgroundBrush().color() == QColor("#000000")

    video_area.set_immersive_background(False)
    assert video_area._scene.backgroundBrush().color() == QColor("#abcdef")


# ------------------------------------------------------------------
# VideoArea – HDR detection & SDR fallback
# ------------------------------------------------------------------

def test_sdr_item_hidden_by_default(qapp):
    """The SDR fallback item should be hidden on construction."""
    video_area = VideoArea()
    assert not video_area._sdr_item.isVisible()
    assert video_area._video_item.isVisible()


def test_hdr_detection_activates_sdr_fallback(qapp, mocker):
    """When an HDR frame arrives, the video item should be hidden and SDR item shown."""
    video_area = VideoArea()

    # Create a mock frame with HDR format
    mock_frame = MagicMock(spec=QVideoFrame)
    mock_frame.isValid.return_value = True
    mock_fmt = MagicMock(spec=QVideoFrameFormat)
    mock_fmt.colorSpace.return_value = QVideoFrameFormat.ColorSpace.ColorSpace_BT2020
    mock_fmt.colorTransfer.return_value = QVideoFrameFormat.ColorTransfer.ColorTransfer_STD_B67
    mock_frame.surfaceFormat.return_value = mock_fmt

    # Provide a non-null QImage from toImage()
    test_img = QImage(320, 240, QImage.Format.Format_ARGB32)
    test_img.fill(QColor("red"))
    mock_frame.toImage.return_value = test_img

    video_area._on_video_frame_changed(mock_frame)

    assert video_area._hdr_detected is True
    assert not video_area._video_item.isVisible()
    assert video_area._sdr_item.isVisible()


def test_sdr_content_keeps_video_item(qapp):
    """When an SDR frame arrives, the standard video item should remain visible."""
    video_area = VideoArea()

    mock_frame = MagicMock(spec=QVideoFrame)
    mock_frame.isValid.return_value = True
    mock_fmt = MagicMock(spec=QVideoFrameFormat)
    mock_fmt.colorSpace.return_value = QVideoFrameFormat.ColorSpace.ColorSpace_BT709
    mock_fmt.colorTransfer.return_value = QVideoFrameFormat.ColorTransfer.ColorTransfer_BT709
    mock_frame.surfaceFormat.return_value = mock_fmt

    video_area._on_video_frame_changed(mock_frame)

    assert video_area._hdr_detected is False
    assert video_area._video_item.isVisible()
    assert not video_area._sdr_item.isVisible()


def test_load_video_resets_hdr_state(qapp, mocker):
    """load_video should reset the HDR detection state."""
    video_area = VideoArea()
    video_area._hdr_detected = True
    video_area._hdr_checked = True
    video_area._video_item.hide()
    video_area._sdr_item.show()

    mocker.patch.object(video_area._player, "setSource")
    mocker.patch.object(video_area._player, "setPosition")

    video_area.load_video(Path("/fake/video.mp4"))

    assert video_area._hdr_detected is False
    assert video_area._hdr_checked is False
    assert video_area._video_item.isVisible()
    assert not video_area._sdr_item.isVisible()


# ------------------------------------------------------------------
# VideoArea – existing tests
# ------------------------------------------------------------------

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


def test_scene_contains_video_and_sdr_items(qapp):
    """The scene should contain the video item and the SDR fallback item."""
    video_area = VideoArea()

    items = video_area._scene.items()
    assert video_area._video_item in items
    assert video_area._sdr_item in items
