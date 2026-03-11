"""Tests for VideoArea widget (QRhiWidget-based architecture)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimedia", reason="QtMultimedia is required")

from unittest.mock import MagicMock
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QShowEvent
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoFrameFormat
from PySide6.QtWidgets import QApplication, QRhiWidget

from iPhoto.config import VIDEO_COMPLETE_HOLD_BACKSTEP_MS
from iPhoto.gui.ui.widgets.video_area import VideoArea
from iPhoto.gui.ui.widgets.video_renderer_widget import (
    VideoRendererWidget,
    _classify_frame_format,
    _FMT_NV12,
    _FMT_P010,
    _FMT_RGBA,
    _CS_BT601,
    _CS_BT709,
    _CS_BT2020,
    _TF_SDR,
    _TF_PQ,
    _TF_HLG,
    _RANGE_LIMITED,
    _RANGE_FULL,
)


@pytest.fixture
def qapp():
    """Create QApplication instance for Qt tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ------------------------------------------------------------------
# Frame format classification
# ------------------------------------------------------------------

class TestClassifyFrameFormat:
    """Tests for _classify_frame_format helper."""

    def test_bt709_sdr_limited(self, qapp):
        """BT.709 SDR limited-range should be classified correctly."""
        fmt = QVideoFrameFormat()
        fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT709)
        fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_BT709)
        pixel, cs, tf, rng = _classify_frame_format(fmt)
        assert cs == _CS_BT709
        assert tf == _TF_SDR
        assert rng == _RANGE_LIMITED

    def test_bt2020_hlg(self, qapp):
        """BT.2020 + HLG should be classified as HDR."""
        fmt = QVideoFrameFormat()
        fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT2020)
        fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_STD_B67)
        pixel, cs, tf, rng = _classify_frame_format(fmt)
        assert cs == _CS_BT2020
        assert tf == _TF_HLG

    def test_bt2020_pq(self, qapp):
        """BT.2020 + PQ (ST.2084) should be classified as HDR-10."""
        fmt = QVideoFrameFormat()
        fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT2020)
        fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_ST2084)
        pixel, cs, tf, rng = _classify_frame_format(fmt)
        assert cs == _CS_BT2020
        assert tf == _TF_PQ

    def test_bt601(self, qapp):
        """BT.601 should be classified correctly."""
        fmt = QVideoFrameFormat()
        fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT601)
        pixel, cs, tf, rng = _classify_frame_format(fmt)
        assert cs == _CS_BT601

    def test_full_range(self, qapp):
        """Full colour range should be classified correctly."""
        fmt = QVideoFrameFormat()
        fmt.setColorRange(QVideoFrameFormat.ColorRange.ColorRange_Full)
        pixel, cs, tf, rng = _classify_frame_format(fmt)
        assert rng == _RANGE_FULL

    def test_default_undefined(self, qapp):
        """Default/undefined format should fall back to safe defaults."""
        fmt = QVideoFrameFormat()
        pixel, cs, tf, rng = _classify_frame_format(fmt)
        assert cs == _CS_BT709
        assert tf == _TF_SDR


# ------------------------------------------------------------------
# VideoRendererWidget
# ------------------------------------------------------------------

class TestVideoRendererWidget:
    """Tests for the VideoRendererWidget class."""

    def test_initial_state(self, qapp):
        """Widget should start with no frame and an empty native size."""
        w = VideoRendererWidget()
        assert w.native_size().isEmpty()

    def test_set_letterbox_color(self, qapp):
        """set_letterbox_color should update the stored color."""
        w = VideoRendererWidget()
        w.set_letterbox_color(QColor("#ff0000"))
        assert w._letterbox_color == QColor("#ff0000")

    def test_clear_frame(self, qapp):
        """clear_frame should reset state."""
        w = VideoRendererWidget()
        w.clear_frame()
        assert w._current_frame is None
        assert w.native_size().isEmpty()


# ------------------------------------------------------------------
# VideoArea – construction & public API
# ------------------------------------------------------------------

class TestVideoArea:
    """Tests for the VideoArea widget."""

    def test_construction(self, qapp):
        """VideoArea should construct without errors."""
        va = VideoArea()
        assert va._renderer is not None
        assert isinstance(va._renderer, VideoRendererWidget)

    def test_renderer_is_child(self, qapp):
        """The renderer should be a child widget of VideoArea."""
        va = VideoArea()
        assert va._renderer.parent() is va

    def test_has_video_sink(self, qapp):
        """VideoArea should use QVideoSink, not QGraphicsVideoItem."""
        va = VideoArea()
        assert va._video_sink is not None

    def test_renderer_uses_opengl_api(self, qapp):
        """VideoRendererWidget must use the OpenGL backend to coexist with QOpenGLWidget."""
        va = VideoArea()
        assert va._renderer.api() == QRhiWidget.Api.OpenGL

    def test_opaque_widget_attributes(self, qapp):
        """VideoArea and renderer must block WA_TranslucentBackground cascade."""
        va = VideoArea()
        assert not va.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert va.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        assert not va._renderer.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert va._renderer.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def test_surface_color_updates_letterbox(self, qapp):
        """set_surface_color should update the renderer's letterbox color."""
        va = VideoArea()
        va.set_surface_color("#abcdef")
        assert va._renderer._letterbox_color == QColor("#abcdef")
        assert va._default_surface_color == "#abcdef"

    def test_immersive_background(self, qapp):
        """set_immersive_background should toggle between black and theme."""
        va = VideoArea()
        va.set_surface_color("#f0f0f0")

        va.set_immersive_background(True)
        assert va._renderer._letterbox_color == QColor("#000000")

        va.set_immersive_background(False)
        assert va._renderer._letterbox_color == QColor("#f0f0f0")

    def test_video_view_returns_renderer(self, qapp):
        """video_view() should return the VideoRendererWidget."""
        va = VideoArea()
        assert va.video_view() is va._renderer

    def test_video_viewport_returns_renderer(self, qapp):
        """video_viewport() should return the VideoRendererWidget."""
        va = VideoArea()
        assert va.video_viewport() is va._renderer

    def test_player_bar_accessible(self, qapp):
        """player_bar property should return the PlayerBar instance."""
        va = VideoArea()
        assert va.player_bar is va._player_bar

    def test_show_event_calls_update_bar_geometry(self, qapp, mocker):
        """showEvent should call _update_bar_geometry."""
        va = VideoArea()
        mock_update = mocker.patch.object(va, '_update_bar_geometry')
        show_event = QShowEvent()
        va.showEvent(show_event)
        mock_update.assert_called_once()

    def test_show_event_calls_super(self, qapp, mocker):
        """showEvent should call the parent class's showEvent."""
        va = VideoArea()
        mock_super_show = mocker.patch('PySide6.QtWidgets.QWidget.showEvent')
        show_event = QShowEvent()
        va.showEvent(show_event)
        mock_super_show.assert_called_once_with(show_event)

    def test_end_of_media_backsteps_and_pauses(self, qapp, mocker):
        """When EndOfMedia fires, the player should backstep and pause."""
        va = VideoArea()

        mocker.patch.object(va._player, "duration", return_value=5000)
        mocker.patch.object(va._player, "position", return_value=5000)
        mock_set_pos = mocker.patch.object(va._player, "setPosition")
        mock_pause = mocker.patch.object(va._player, "pause")

        va._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)

        mock_set_pos.assert_called_once_with(5000 - VIDEO_COMPLETE_HOLD_BACKSTEP_MS)
        mock_pause.assert_called_once()

    def test_load_video_clears_frame(self, qapp, mocker):
        """load_video should clear the renderer frame."""
        va = VideoArea()
        mocker.patch.object(va._player, "setSource")
        mocker.patch.object(va._player, "setPosition")
        mock_clear = mocker.patch.object(va._renderer, "clear_frame")

        va.load_video(Path("/fake/video.mp4"))

        mock_clear.assert_called_once()
