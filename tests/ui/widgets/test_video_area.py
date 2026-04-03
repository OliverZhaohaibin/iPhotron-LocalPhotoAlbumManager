"""Tests for VideoArea widget (QRhiWidget-based architecture)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimedia", reason="QtMultimedia is required")

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QShowEvent
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoFrameFormat
from PySide6.QtWidgets import QApplication, QRhiWidget

from iPhoto.config import VIDEO_COMPLETE_HOLD_BACKSTEP_MS
from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer
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


def _set_rotation_180(fmt: QVideoFrameFormat) -> None:
    """Set 180° rotation in a Qt-version-compatible way."""
    rot_enum = getattr(QVideoFrameFormat, "Rotation", None)
    if rot_enum is not None and hasattr(rot_enum, "Clockwise180"):
        fmt.setRotation(rot_enum.Clockwise180)
        return

    try:
        from PySide6.QtMultimedia import QtVideo

        if hasattr(QtVideo, "Rotation") and hasattr(QtVideo.Rotation, "Clockwise180"):
            fmt.setRotation(QtVideo.Rotation.Clockwise180)
            return
    except (ModuleNotFoundError, ImportError):
        pass

    # Last-resort fallback for bindings that expose a different enum path.
    for enum_name in ("Rotated180", "Clockwise180"):
        rotation_value = getattr(fmt.rotation(), enum_name, None)
        if rotation_value is not None:
            fmt.setRotation(rotation_value)
            return

    raise RuntimeError("Could not resolve a Qt-compatible 180° rotation enum")


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

    def test_initial_has_frame_false(self, qapp):
        """Widget should start with _has_frame == False."""
        w = VideoRendererWidget()
        assert w._has_frame is False

    def test_clear_frame_resets_has_frame(self, qapp):
        """clear_frame should set _has_frame to False so the renderer
        draws only the letterbox colour instead of stale texture data."""
        w = VideoRendererWidget()
        # Simulate having received a frame
        w._has_frame = True
        w.clear_frame()
        assert w._has_frame is False
        assert w._frame_dirty is False

    def test_set_container_rotation(self, qapp):
        """set_container_rotation should store the probed values."""
        w = VideoRendererWidget()
        w.set_container_rotation(90, 1920, 1440)
        assert w._container_rotation_cw == 90
        assert w._container_raw_w == 1920
        assert w._container_raw_h == 1440

    def test_clear_frame_resets_container_rotation(self, qapp):
        """clear_frame should also reset the container rotation state."""
        w = VideoRendererWidget()
        w.set_container_rotation(90, 1920, 1440)
        w.clear_frame()
        assert w._container_rotation_cw == 0
        assert w._container_raw_w == 0
        assert w._container_raw_h == 0

    def test_fallback_rotation_when_qt_reports_zero(self, qapp):
        """When Qt reports 0° but container has rotation, apply fallback."""
        w = VideoRendererWidget()
        w.set_container_rotation(90, 1920, 1440)

        # Create a frame with dimensions matching the raw stream (not pre-rotated)
        from PySide6.QtCore import QSize
        fmt = QVideoFrameFormat(
            QSize(1920, 1440), QVideoFrameFormat.PixelFormat.Format_RGBA8888
        )
        # Qt default rotation is 0°
        frame = QVideoFrame(fmt)
        w.update_frame(frame)

        # Container rotation 90° CW → steps = 1
        assert w._rotate90_steps == 1

    def test_no_double_rotation_when_prerotated(self, qapp):
        """When GStreamer pre-rotates frames, do not apply container rotation again."""
        w = VideoRendererWidget()
        w.set_container_rotation(90, 1920, 1440)

        # Frame dimensions are swapped compared to raw stream → pre-rotated
        from PySide6.QtCore import QSize
        fmt = QVideoFrameFormat(
            QSize(1440, 1920), QVideoFrameFormat.PixelFormat.Format_RGBA8888
        )
        frame = QVideoFrame(fmt)
        w.update_frame(frame)

        # Pre-rotated → no additional rotation
        assert w._rotate90_steps == 0

    def test_no_double_rotation_for_linux_180_prerotated(self, qapp, mocker):
        """Linux-specific 180° clips should not be rotated twice."""
        w = VideoRendererWidget()
        w.set_container_rotation(180, 1280, 720)

        mocker.patch("iPhoto.gui.ui.widgets.video_renderer_widget.sys.platform", "linux")
        mocker.patch.dict(
            "iPhoto.gui.ui.widgets.video_renderer_widget.os.environ",
            {"QT_MEDIA_BACKEND": "gstreamer"},
            clear=False,
        )

        from PySide6.QtCore import QSize
        fmt = QVideoFrameFormat(
            QSize(1280, 720), QVideoFrameFormat.PixelFormat.Format_RGBA8888
        )
        _set_rotation_180(fmt)
        frame = QVideoFrame(fmt)
        w.update_frame(frame)

        # Heuristic should treat this as pre-rotated.
        assert w._rotate90_steps == 0

    def test_linux_180_without_backend_hint_keeps_container_rotation(self, qapp, mocker):
        """Linux 180° streams should still rotate when no pre-rotation hint exists."""
        w = VideoRendererWidget()
        w.set_container_rotation(180, 1280, 720)

        mocker.patch("iPhoto.gui.ui.widgets.video_renderer_widget.sys.platform", "linux")
        mocker.patch.dict(
            "iPhoto.gui.ui.widgets.video_renderer_widget.os.environ",
            {},
            clear=True,
        )

        from PySide6.QtCore import QSize
        fmt = QVideoFrameFormat(
            QSize(1280, 720), QVideoFrameFormat.PixelFormat.Format_RGBA8888
        )
        _set_rotation_180(fmt)
        frame = QVideoFrame(fmt)
        w.update_frame(frame)

        # No backend hint/override -> apply container 180° correction.
        assert w._rotate90_steps == 2

    def test_linux_180_with_container_hint_skips_rotation(self, qapp, mocker):
        """Container hint should allow Linux 180° pre-rotation detection."""
        w = VideoRendererWidget()
        w.set_container_rotation(180, 1280, 720, linux_180_hint=True)

        mocker.patch("iPhoto.gui.ui.widgets.video_renderer_widget.sys.platform", "linux")
        mocker.patch.dict(
            "iPhoto.gui.ui.widgets.video_renderer_widget.os.environ",
            {},
            clear=True,
        )

        from PySide6.QtCore import QSize
        fmt = QVideoFrameFormat(
            QSize(1280, 720), QVideoFrameFormat.PixelFormat.Format_RGBA8888
        )
        _set_rotation_180(fmt)
        frame = QVideoFrame(fmt)
        w.update_frame(frame)

        assert w._rotate90_steps == 0

    def test_no_fallback_when_no_container_rotation(self, qapp):
        """When container has no rotation, steps stay at 0."""
        w = VideoRendererWidget()
        w.set_container_rotation(0, 1920, 1440)

        from PySide6.QtCore import QSize
        fmt = QVideoFrameFormat(
            QSize(1920, 1440), QVideoFrameFormat.PixelFormat.Format_RGBA8888
        )
        frame = QVideoFrame(fmt)
        w.update_frame(frame)
        assert w._rotate90_steps == 0

    def test_container_rotation_overrides_qt_rotation(self, qapp):
        """ffprobe rotation is always preferred over Qt's platform-dependent value."""
        w = VideoRendererWidget()
        # Container says 90° CW (correct for a -90° CCW display matrix).
        w.set_container_rotation(90, 1920, 1440)

        from PySide6.QtCore import QSize
        fmt = QVideoFrameFormat(
            QSize(1920, 1440), QVideoFrameFormat.PixelFormat.Format_RGBA8888
        )
        frame = QVideoFrame(fmt)
        w.update_frame(frame)

        # Container rotation (90° CW) wins → steps = 1
        assert w._rotate90_steps == 1

    def test_update_frame_sets_has_frame(self, qapp):
        """update_frame should set _has_frame to True when a valid frame arrives."""
        w = VideoRendererWidget()
        assert w._has_frame is False

        from PySide6.QtCore import QSize
        fmt = QVideoFrameFormat(
            QSize(320, 240), QVideoFrameFormat.PixelFormat.Format_RGBA8888
        )
        frame = QVideoFrame(fmt)
        w.update_frame(frame)
        assert w._has_frame is True

    def test_update_frame_ignores_invalid(self, qapp):
        """update_frame should leave _has_frame unchanged for invalid frames."""
        w = VideoRendererWidget()
        assert w._has_frame is False
        w.update_frame(None)
        assert w._has_frame is False

    def test_clear_frame_resets_texture_formats(self, qapp):
        """clear_frame should reset the tracked Y/UV texture formats so
        that switching between NV12 (8-bit R8) and P010 (10-bit R16) at the
        same resolution forces texture recreation."""
        w = VideoRendererWidget()
        # Simulate having uploaded an NV12 frame (sets tracked formats)
        from PySide6.QtGui import QRhiTexture

        w._tex_y_fmt = QRhiTexture.Format.R8
        w._tex_uv_fmt = QRhiTexture.Format.RG8
        w.clear_frame()
        assert w._tex_y_fmt is None
        assert w._tex_uv_fmt is None

    def test_initial_texture_formats_are_none(self, qapp):
        """Texture format tracking should start as None so the first video
        always creates textures with the correct format."""
        w = VideoRendererWidget()
        assert w._tex_y_fmt is None
        assert w._tex_uv_fmt is None


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
        """VideoRendererWidget must use the OpenGL backend (same as GLImageViewer)."""
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

    def test_play_restarts_when_paused_on_end_hold_frame(self, qapp, mocker):
        """Pressing play after auto-pause at the end should restart from 0."""
        va = VideoArea()

        mocker.patch.object(va._player, "duration", return_value=5000)
        mocker.patch.object(
            va._player,
            "position",
            return_value=5000 - VIDEO_COMPLETE_HOLD_BACKSTEP_MS,
        )
        mocker.patch.object(
            va._player,
            "playbackState",
            return_value=QMediaPlayer.PlaybackState.PausedState,
        )
        mock_set_pos = mocker.patch.object(va._player, "setPosition")
        mock_play = mocker.patch.object(va._player, "play")

        va.play()

        mock_set_pos.assert_called_once_with(0)
        mock_play.assert_called_once()

    def test_load_video_clears_frame(self, qapp, mocker):
        """load_video should clear the renderer frame."""
        va = VideoArea()
        mocker.patch.object(va._player, "setSource")
        mocker.patch.object(va._player, "setPosition")
        mock_clear = mocker.patch.object(va._renderer, "clear_frame")
        mocker.patch(
            "iPhoto.gui.ui.widgets.video_area.probe_video_rotation",
            return_value=(0, 0, 0),
        )

        va.load_video(Path("/fake/video.mp4"))

        mock_clear.assert_called_once()

    def test_load_video_probes_and_sets_container_rotation(self, qapp, mocker):
        """load_video should probe rotation and forward to the renderer."""
        va = VideoArea()
        mocker.patch.object(va._player, "setSource")
        mocker.patch.object(va._player, "setPosition")
        mocker.patch.object(va._renderer, "clear_frame")
        mock_set_rot = mocker.patch.object(va._renderer, "set_container_rotation")
        mocker.patch(
            "iPhoto.gui.ui.widgets.video_area.probe_video_rotation",
            return_value=(90, 1920, 1440),
        )

        va.load_video(Path("/fake/portrait.mov"))

        mock_set_rot.assert_called_once_with(90, 1920, 1440)

    def test_load_video_handles_probe_failure(self, qapp, mocker):
        """load_video should still work when ffprobe returns no rotation."""
        va = VideoArea()
        mocker.patch.object(va._player, "setSource")
        mocker.patch.object(va._player, "setPosition")
        mocker.patch.object(va._renderer, "clear_frame")
        mock_set_rot = mocker.patch.object(va._renderer, "set_container_rotation")
        mocker.patch(
            "iPhoto.gui.ui.widgets.video_area.probe_video_rotation",
            return_value=(0, 0, 0),
        )

        va.load_video(Path("/fake/video.mp4"))

        mock_set_rot.assert_called_once_with(0, 0, 0)

    def test_stop_clears_frame_and_source(self, qapp, mocker):
        """stop() should clear the renderer frame and release the media source."""
        va = VideoArea()
        mock_stop = mocker.patch.object(va._player, "stop")
        mock_set_source = mocker.patch.object(va._player, "setSource")
        mock_clear = mocker.patch.object(va._renderer, "clear_frame")

        va.stop()

        mock_stop.assert_called_once()
        # Source should be cleared (empty QUrl)
        mock_set_source.assert_called_once()
        called_url = mock_set_source.call_args[0][0]
        assert called_url.isEmpty()
        # Renderer frame should be cleared
        mock_clear.assert_called_once()

    def test_adjusted_preview_uses_direct_video_frame_path(self, qapp, mocker):
        """Adjusted video preview should bypass QImage conversion."""
        va = VideoArea()
        va.set_adjusted_preview_enabled(True)

        frame = mocker.Mock()
        frame.isValid.return_value = True
        mock_set_video_frame = mocker.patch.object(va._edit_viewer, "set_video_frame")
        mock_to_image = mocker.patch.object(frame, "toImage")

        va._on_video_frame(frame)

        mock_set_video_frame.assert_called_once()
        mock_to_image.assert_not_called()


def test_gl_image_viewer_reuses_adjustments_for_successive_video_frames(qapp, mocker):
    """Streaming frames with unchanged adjustments should not rebuild LUT state."""

    viewer = GLImageViewer()
    fmt = QVideoFrameFormat(QSize(320, 240), QVideoFrameFormat.PixelFormat.Format_RGBA8888)
    frame = QVideoFrame(fmt)
    viewer._adjustments = {"Exposure": 0.25}

    mock_set_adjustments = mocker.patch.object(viewer, "set_adjustments")

    viewer.set_video_frame(frame, {"Exposure": 0.25}, reset_view=False)

    mock_set_adjustments.assert_not_called()
    assert viewer._video_frame is frame
    assert viewer._video_frame_dirty is True
