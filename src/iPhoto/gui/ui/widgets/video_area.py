"""Widget combining the video surface and floating playback controls."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QPropertyAnimation,
    QSizeF,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QMouseEvent,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QStackedWidget,
    QWidget,
)

try:  # pragma: no cover - optional Qt module
    from PySide6.QtMultimedia import (
        QAudioOutput,
        QMediaPlayer,
        QVideoFrame,
        QVideoSink,
    )
except (ModuleNotFoundError, ImportError):  # pragma: no cover - handled by main window guard
    QMediaPlayer = None
    QAudioOutput = None
    QVideoFrame = None  # type: ignore[assignment, misc]
    QVideoSink = None  # type: ignore[assignment, misc]

from ....config import (
    PLAYER_CONTROLS_HIDE_DELAY_MS,
    PLAYER_FADE_IN_MS,
    PLAYER_FADE_OUT_MS,
    VIDEO_COMPLETE_HOLD_BACKSTEP_MS,
)
from ....core.adjustment_mapping import normalise_video_trim
from ....utils.ffmpeg import get_linux_180_prerotate_hint, probe_video_rotation
from ..palette import viewer_surface_color
from .gl_image_viewer import GLImageViewer
from .player_bar import PlayerBar
from .video_renderer_widget import VideoRendererWidget, _resolve_frame_rotation_cw

_log = logging.getLogger(__name__)


class VideoArea(QWidget):
    """Present a video surface with auto-hiding playback controls.

    Uses :class:`VideoRendererWidget` (``QRhiWidget``) for GPU-accelerated
    rendering with proper colour-science handling: YUV→RGB conversion,
    correct BT.601/709/2020 matrix selection, limited/full range, and
    HDR→SDR tone mapping for PQ (ST.2084) and HLG (STD-B67) content.

    Decoded frames are received from a ``QVideoSink`` and uploaded as GPU
    textures.  The rendering result is always fully opaque (alpha = 1.0),
    independent of any parent-widget background colour.
    """

    mouseActive = Signal()
    controlsVisibleChanged = Signal(bool)
    fullscreenExitRequested = Signal()
    playbackStateChanged = Signal(bool)
    playbackFinished = Signal()
    nextItemRequested = Signal()
    prevItemRequested = Signal()
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    zoomChanged = Signal(float)
    cropChanged = Signal(float, float, float, float)
    cropInteractionStarted = Signal()
    cropInteractionFinished = Signal()
    colorPicked = Signal(float, float, float)
    firstFrameReady = Signal()
    displaySizeChanged = Signal(QSizeF)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Prevent the WA_TranslucentBackground cascade from the main window
        # from making the video surface transparent.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)

        if QMediaPlayer is None or QVideoSink is None:
            raise RuntimeError(
                "PySide6.QtMultimedia is required for video playback."
            )

        # --- Video Renderer Setup ---
        surface_color = viewer_surface_color(self)
        self._default_surface_color = surface_color

        self._surface_stack = QStackedWidget(self)
        self._renderer = VideoRendererWidget(self._surface_stack)
        self._renderer.set_letterbox_color(QColor(surface_color))
        # Ensure the renderer is also opaque and doesn't inherit transparency.
        self._renderer.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._renderer.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        # Accept focus so keyboard navigation targets the video surface
        # without requiring the user to click a non-interactive element.
        self._renderer.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._edit_viewer = GLImageViewer(self._surface_stack)
        self._edit_viewer.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Playback should keep the legacy detail-page proportions: the crop is
        # centered, but we do not zoom it to fill the viewport. Edit mode opts
        # back into crop framing on the same shared GL surface.
        self._edit_viewer.set_crop_framing_enabled(False)
        self._surface_stack.addWidget(self._renderer)
        self._surface_stack.addWidget(self._edit_viewer)
        self._surface_stack.setCurrentWidget(self._renderer)
        self.setFocusProxy(self._renderer)

        self._adjusted_preview_enabled = False
        self._edit_mode_active = False
        self._current_adjustments: dict[str, object] = {}
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._current_duration_ms = 0
        self._current_source: Path | None = None
        self._container_rotation_cw = 0
        self._container_raw_w = 0
        self._container_raw_h = 0
        self._container_linux_180_hint = False
        self._adjusted_first_frame_pending = False
        self._suppress_trim_pause = False
        self._restart_from_trim_in_on_play = False
        self._end_hold_display_ms: int | None = None
        self._transparent_preview_enabled = False

        self._apply_surface(surface_color)
        # --- End Video Renderer Setup ---

        # --- Media Player Setup ---
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)

        # Route decoded frames through QVideoSink → our custom renderer.
        self._video_sink = QVideoSink(self)
        self._player.setVideoOutput(self._video_sink)
        self._video_sink.videoFrameChanged.connect(self._on_video_frame)

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        # --- End Media Player Setup ---

        self._overlay_margin = 48
        self._player_bar = PlayerBar(self)
        self._player_bar.hide()
        self._player_bar.setMouseTracking(True)

        self._controls_visible = False
        self._target_opacity = 0.0
        self._host_widget: QWidget | None = self._renderer
        self._window_host: QWidget | None = None
        self._controls_enabled = True

        effect = QGraphicsOpacityEffect(self._player_bar)
        effect.setOpacity(0.0)
        self._player_bar.setGraphicsEffect(effect)

        self._fade_anim = QPropertyAnimation(effect, b"opacity", self)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_anim.finished.connect(self._on_fade_finished)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(PLAYER_CONTROLS_HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self.hide_controls)

        self._install_activity_filters()
        self._wire_player_bar()
        self._wire_edit_viewer()
        self._renderer.nativeSizeChanged.connect(self.displaySizeChanged.emit)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def renderer(self) -> VideoRendererWidget:
        """Return the :class:`VideoRendererWidget` for direct access."""

        return self._renderer

    @property
    def player_bar(self) -> PlayerBar:
        """Return the floating :class:`PlayerBar`."""

        return self._player_bar

    @property
    def edit_viewer(self) -> GLImageViewer:
        """Expose the GL-based adjusted video preview surface."""

        return self._edit_viewer

    def adjusted_preview_enabled(self) -> bool:
        """Return whether decoded frames are routed through the GL edit viewer."""

        return self._adjusted_preview_enabled

    def set_adjusted_preview_enabled(self, enabled: bool) -> None:
        """Route decoded frames through the adjusted GL preview when *enabled*."""

        target = bool(enabled)
        if self._adjusted_preview_enabled == target:
            return
        self._adjusted_preview_enabled = target
        self._surface_stack.setCurrentWidget(self._edit_viewer if target else self._renderer)
        self.setFocusProxy(self._edit_viewer if target else self._renderer)
        if target:
            self._adjusted_first_frame_pending = True
            self._edit_viewer.set_adjustments(self._current_adjustments)
            if self._current_duration_ms > 0:
                self._edit_viewer.update()
        else:
            self._edit_mode_active = False

    def set_edit_mode_active(self, active: bool) -> None:
        """Mark whether the video area is currently being used inside Edit mode."""

        self._edit_mode_active = bool(active)
        self._edit_viewer.set_crop_framing_enabled(self._edit_mode_active)
        if self._edit_mode_active:
            self.set_adjusted_preview_enabled(True)

    def set_adjustments(self, adjustments: Mapping[str, object] | None = None) -> None:
        """Apply GL adjustments to the adjusted preview surface."""

        self._current_adjustments = dict(adjustments or {})
        self._edit_viewer.set_adjustments(self._current_adjustments)

    def set_trim_range_ms(self, trim_in_ms: int, trim_out_ms: int) -> None:
        """Update the active in/out points in milliseconds."""

        self._restart_from_trim_in_on_play = False
        self._end_hold_display_ms = None
        duration = max(int(self._current_duration_ms), 0)
        if duration > 0:
            trim_in, trim_out = normalise_video_trim(
                {
                    "Video_Trim_In_Sec": max(trim_in_ms, 0) / 1000.0,
                    "Video_Trim_Out_Sec": max(trim_out_ms, 0) / 1000.0,
                },
                duration / 1000.0,
            )
            self._trim_in_ms = int(round(trim_in * 1000.0))
            self._trim_out_ms = int(round(trim_out * 1000.0))
        else:
            self._trim_in_ms = max(int(trim_in_ms), 0)
            self._trim_out_ms = max(int(trim_out_ms), self._trim_in_ms)
        current_pos = self._player.position()
        clamped_pos = current_pos
        if current_pos < self._trim_in_ms:
            clamped_pos = self._trim_in_ms
        elif self._trim_out_ms > 0 and current_pos > self._trim_out_ms:
            clamped_pos = self._trim_out_ms
        if clamped_pos != current_pos:
            self._player.setPosition(clamped_pos)
            self._sync_position_display(clamped_pos)

    def trim_range_ms(self) -> tuple[int, int]:
        """Return the current trim range in milliseconds."""

        return (self._trim_in_ms, self._trim_out_ms)

    def setCropMode(self, enabled: bool, values=None) -> None:
        """Proxy crop mode toggling to the adjusted preview surface."""

        self.set_adjusted_preview_enabled(True)
        self._edit_viewer.setCropMode(enabled, values)

    def crop_values(self) -> dict[str, float]:
        """Return the current crop mapping from the adjusted preview surface."""

        return self._edit_viewer.crop_values()

    def start_perspective_interaction(self) -> None:
        self._edit_viewer.start_perspective_interaction()

    def end_perspective_interaction(self) -> None:
        self._edit_viewer.end_perspective_interaction()

    def set_crop_aspect_ratio(self, ratio: float) -> None:
        self._edit_viewer.set_crop_aspect_ratio(ratio)

    def rotate_image_ccw(self) -> dict[str, float]:
        self.set_adjusted_preview_enabled(True)
        return self._edit_viewer.rotate_image_ccw()

    def set_zoom(self, factor: float, anchor: QPointF | None = None) -> None:
        self._edit_viewer.set_zoom(factor, anchor=anchor or self.viewport_center())

    def reset_zoom(self) -> None:
        self._edit_viewer.reset_zoom()

    def zoom_in(self) -> None:
        self._edit_viewer.zoom_in()

    def zoom_out(self) -> None:
        self._edit_viewer.zoom_out()

    def viewport_center(self) -> QPointF:
        return self._edit_viewer.viewport_center()

    def set_eyedropper_mode(self, active: bool) -> None:
        self._edit_viewer.set_eyedropper_mode(active)

    def set_immersive_background(self, immersive: bool) -> None:
        """Switch to a pure black canvas when immersive full screen mode is active."""

        colour = "#000000" if immersive else self._default_surface_color
        self._apply_surface(colour)

    def set_surface_color(self, colour: str) -> None:
        """Update the surface colour used for letterbox and background areas.

        Called by the theme controller whenever the application theme changes
        so that the video canvas stays in sync with the surrounding chrome.
        """

        self._default_surface_color = colour
        self._apply_surface(colour)

    def set_viewport_fill_enabled(self, enabled: bool) -> None:
        """Control whether preview surfaces cover the viewport instead of fitting inside it."""

        self._renderer.set_viewport_fill_enabled(enabled)
        self._edit_viewer.set_viewport_fill_enabled(enabled)

    def set_transparent_preview_enabled(
        self,
        enabled: bool,
        *,
        corner_radius: float = 0.0,
    ) -> None:
        """Enable a translucent preview surface with shader-rounded corners."""

        target = bool(enabled)
        self._transparent_preview_enabled = target
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, target)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, not target)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, target)
        self.setAutoFillBackground(not target)
        self._surface_stack.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, target)
        self._surface_stack.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, target)
        self._surface_stack.setAutoFillBackground(not target)
        if target:
            self._surface_stack.setStyleSheet("background: transparent; border: none;")
        else:
            self._surface_stack.setStyleSheet("")
        self._edit_viewer.set_transparent_rounded_clip(corner_radius if target else 0.0)
        self._apply_surface(self._default_surface_color)

    def _apply_surface(self, colour: str) -> None:
        """Apply *colour* to the renderer letterbox, widget, and stylesheet."""

        self._renderer.set_letterbox_color(QColor(colour))
        self._edit_viewer.set_surface_color_override(colour)
        background = "transparent" if self._transparent_preview_enabled else colour
        self.setStyleSheet(f"background-color: {background}; border: none;")

    def show_controls(self, *, animate: bool = True) -> None:
        """Reveal the playback controls and restart the hide timer."""

        if not self._controls_enabled:
            return
        self._hide_timer.stop()
        if not self._controls_visible:
            self._controls_visible = True
            self.controlsVisibleChanged.emit(True)

        if not self._player_bar.isVisible():
            self._player_bar.show()
            self._update_bar_geometry()

        duration = PLAYER_FADE_IN_MS if animate else 0
        self._animate_to(1.0, duration)
        self._restart_hide_timer()

    def hide_controls(self, *, animate: bool = True) -> None:
        """Fade the playback controls out."""

        if not self._controls_visible and self._current_opacity() <= 0.0:
            return
        self._hide_timer.stop()
        if self._controls_visible:
            self._controls_visible = False
            self.controlsVisibleChanged.emit(False)

        duration = PLAYER_FADE_OUT_MS if animate else 0
        self._animate_to(0.0, duration)

    def note_activity(self) -> None:
        """Treat external events as user activity to keep controls visible."""

        if not self._controls_enabled:
            return
        if self._controls_visible:
            self._restart_hide_timer()
        else:
            self.show_controls()

    # ------------------------------------------------------------------
    # Player Control API
    # ------------------------------------------------------------------
    def set_volume(self, volume: int) -> None:
        """Update the audio output volume (0-100)."""
        clamped = max(0, min(100, volume))
        self._audio_output.setVolume(clamped / 100.0)
        self._player_bar.set_volume(clamped)

    def set_muted(self, muted: bool) -> None:
        """Update the audio output mute state."""
        self._audio_output.setMuted(muted)
        self._player_bar.set_muted(muted)

    def load_video(
        self,
        path: Path,
        *,
        adjustments: Mapping[str, object] | None = None,
        trim_range_ms: tuple[int, int] | None = None,
        adjusted_preview: bool | None = None,
    ) -> None:
        """Load a video file for playback."""

        self._current_source = path
        self._current_adjustments = dict(adjustments or {})
        if adjusted_preview is not None:
            self.set_adjusted_preview_enabled(adjusted_preview)
        if self._adjusted_preview_enabled:
            self._adjusted_first_frame_pending = True
        self._edit_viewer.set_adjustments(self._current_adjustments)
        self._edit_viewer.set_video_source_rotation(0)
        self._edit_viewer.clear()
        self._renderer.clear_frame()
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._current_duration_ms = 0
        self._end_hold_display_ms = None

        # Probe the container-level display-matrix rotation from ffprobe
        # *before* setting the source.  The renderer uses the probed value
        # as the primary rotation source (more reliable across platforms
        # than Qt's ``QVideoFrameFormat.rotation()``).
        cw_deg, raw_w, raw_h = probe_video_rotation(path)
        linux_180_hint = get_linux_180_prerotate_hint(path)
        self._container_rotation_cw = cw_deg
        self._container_raw_w = raw_w
        self._container_raw_h = raw_h
        self._container_linux_180_hint = linux_180_hint
        self._renderer.set_container_rotation(cw_deg, raw_w, raw_h)
        self._renderer.set_linux_180_hint(linux_180_hint)
        if cw_deg:
            _log.debug(
                "Container rotation for %s: %d° CW (raw %dx%d)",
                path.name, cw_deg, raw_w, raw_h,
            )

        if raw_w > 0 and raw_h > 0:
            if cw_deg in (90, 270):
                display_width = raw_h
                display_height = raw_w
            else:
                display_width = raw_w
                display_height = raw_h
            self.displaySizeChanged.emit(QSizeF(float(display_width), float(display_height)))

        self._player.setSource(QUrl.fromLocalFile(str(path)))
        # Do not auto-play; let the coordinator decide.
        # But ensure we are at start
        if trim_range_ms is not None:
            self.set_trim_range_ms(*trim_range_ms)
        self._player.setPosition(self._trim_in_ms if self._trim_in_ms > 0 else 0)

    def play(self) -> None:
        """Start or resume playback."""
        # If playback previously reached ``EndOfMedia`` we keep the last frame
        # visible by stepping back a few milliseconds and pausing.  Pressing
        # play again should restart from the beginning instead of resuming
        # from that hold position.
        duration = self._player.duration()
        position = self._player.position()
        hold_pos = max(0, duration - VIDEO_COMPLETE_HOLD_BACKSTEP_MS)
        if self._restart_from_trim_in_on_play:
            self._player.setPosition(self._trim_in_ms if self._trim_in_ms > 0 else 0)
            self._restart_from_trim_in_on_play = False
            self._end_hold_display_ms = None
        elif (
            duration > 0
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState
            and position >= hold_pos
        ):
            self._player.setPosition(self._trim_in_ms if self._trim_in_ms > 0 else 0)
            self._end_hold_display_ms = None
        elif position < self._trim_in_ms or (
            self._trim_out_ms > 0 and position >= self._trim_out_ms
        ):
            self._player.setPosition(self._trim_in_ms)
            self._end_hold_display_ms = None
        self._player.play()

    def is_playing(self) -> bool:
        """Return whether the underlying media player is actively playing."""

        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def pause(self) -> None:
        """Pause playback."""
        self._player.pause()

    def seek(self, position: int) -> None:
        """Seek to a specific position in milliseconds."""
        self._restart_from_trim_in_on_play = False
        self._end_hold_display_ms = None
        target = int(position)
        if target < self._trim_in_ms:
            target = self._trim_in_ms
        if self._trim_out_ms > self._trim_in_ms and target > self._trim_out_ms:
            target = self._trim_out_ms
        self._player.setPosition(target)

    def stop(self) -> None:
        """Stop playback, release the media source and clear the renderer.

        Clearing the source ensures that the video decoder fully releases its
        resources and that no stale frames are sent through the ``QVideoSink``
        after stopping.  Clearing the renderer removes any residual frame
        texture so that subsequent media transitions never flash the last
        rendered video frame.
        """
        self._player.stop()
        self._player.setSource(QUrl())
        self._renderer.clear_frame()
        self._edit_viewer.clear()
        self._edit_viewer.set_video_source_rotation(0)
        self._current_source = None
        self._current_duration_ms = 0
        self._container_rotation_cw = 0
        self._container_raw_w = 0
        self._container_raw_h = 0
        self._container_linux_180_hint = False
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._restart_from_trim_in_on_play = False
        self._end_hold_display_ms = None

    def _on_video_frame(self, frame: "QVideoFrame") -> None:
        """Forward each decoded frame to the GPU renderer."""
        if self._adjusted_preview_enabled:
            resolved_rotation_cw = _resolve_frame_rotation_cw(
                frame.surfaceFormat(),
                container_rotation_cw=self._container_rotation_cw,
                container_raw_w=self._container_raw_w,
                container_raw_h=self._container_raw_h,
                linux_180_hint=self._container_linux_180_hint,
            )
            self._edit_viewer.set_pending_video_source_rotation(resolved_rotation_cw)
            reset_view = self._adjusted_first_frame_pending
            self._adjusted_first_frame_pending = False
            self._edit_viewer.set_video_frame(
                frame,
                self._current_adjustments,
                reset_view=reset_view,
            )
        else:
            self._renderer.update_frame(frame)

    def _on_position_changed(self, position: int) -> None:
        if self._trim_out_ms > self._trim_in_ms and position >= self._trim_out_ms:
            self._enter_end_hold(
                end_pos=self._trim_out_ms,
                hold_pos=max(
                    self._trim_in_ms,
                    self._trim_out_ms - VIDEO_COMPLETE_HOLD_BACKSTEP_MS,
                ),
            )
            return
        display_position = self._display_position(position)
        if not self._suppress_trim_pause and display_position == position:
            self._restart_from_trim_in_on_play = False
            self._end_hold_display_ms = None
        if not self._suppress_trim_pause:
            self._sync_position_display(display_position)

    def _on_duration_changed(self, duration: int) -> None:
        self._current_duration_ms = int(duration)
        if duration > 0:
            # Clamp trim to [0, duration].  If the result is an invalid range
            # (unset, both zero, or collapsed after clamping), reset to full range.
            clamped_in = min(self._trim_in_ms, int(duration))
            clamped_out = min(self._trim_out_ms, int(duration))
            if clamped_in >= clamped_out:
                self._trim_in_ms = 0
                self._trim_out_ms = int(duration)
            else:
                self._trim_in_ms = clamped_in
                self._trim_out_ms = clamped_out
                current_pos = self._player.position()
                if current_pos > self._trim_out_ms:
                    self._player.setPosition(self._trim_out_ms)
        self._player_bar.set_duration(duration)
        self.durationChanged.emit(duration)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        is_playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self._player_bar.set_playback_state(is_playing)
        self.playbackStateChanged.emit(is_playing)
        if not is_playing and state == QMediaPlayer.PlaybackState.StoppedState:
            self.show_controls()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            duration = self._trim_out_ms if self._trim_out_ms > self._trim_in_ms else self._player.duration()
            if duration <= 0:
                return
            position = self._player.position()
            if position + 200 < duration:
                return
            # Step back a few milliseconds and pause so the last visible
            # frame remains on screen instead of flashing to black.
            self._enter_end_hold(
                end_pos=int(duration),
                hold_pos=max(0, int(duration) - VIDEO_COMPLETE_HOLD_BACKSTEP_MS),
            )

    def _display_position(self, position: int) -> int:
        """Return the timeline position that should be exposed to the UI."""

        if self._restart_from_trim_in_on_play and self._end_hold_display_ms is not None:
            end_pos = self._end_hold_display_ms
            lower_bound = max(self._trim_in_ms, end_pos - VIDEO_COMPLETE_HOLD_BACKSTEP_MS)
            if lower_bound <= position <= end_pos:
                return end_pos
        return position

    def _sync_position_display(self, position: int) -> None:
        """Synchronise the visible timeline position with the current playhead."""

        self._player_bar.set_position(position)
        self.positionChanged.emit(position)

    def _enter_end_hold(self, *, end_pos: int, hold_pos: int) -> None:
        """Pause on the last frame while keeping the timeline cursor at the end."""

        end_pos = max(0, int(end_pos))
        hold_pos = max(0, min(int(hold_pos), end_pos))
        if self._restart_from_trim_in_on_play and self._end_hold_display_ms == end_pos:
            self._sync_position_display(end_pos)
            return
        if self._suppress_trim_pause:
            return
        self._suppress_trim_pause = True
        self._end_hold_display_ms = end_pos
        self._restart_from_trim_in_on_play = True
        self._player.pause()
        self._player.setPosition(hold_pos)
        self._suppress_trim_pause = False
        self._sync_position_display(end_pos)
        self.show_controls()
        self.playbackFinished.emit()

    def _on_volume_changed(self, value: int) -> None:
        """Handle volume changes from the player bar."""
        self._audio_output.setVolume(value / 100.0)
        self._on_mouse_activity()

    def _on_mute_toggled(self, muted: bool) -> None:
        """Handle mute toggle from the player bar."""
        self._audio_output.setMuted(muted)
        self._on_mouse_activity()

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def resizeEvent(self, event: QResizeEvent) -> None:  # pragma: no cover - GUI behaviour
        """Manually layout child widgets."""

        super().resizeEvent(event)
        rect = self.rect()
        self._surface_stack.setGeometry(rect)
        self._update_bar_geometry()

    def enterEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        super().enterEvent(event)
        self.show_controls()

    def leaveEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        super().leaveEvent(event)
        if not self._player_bar.underMouse():
            self.hide_controls()

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        self._on_mouse_activity()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # pragma: no cover - GUI behaviour
        """Emit a dedicated signal so the window can exit immersive full screen."""

        if event.button() == Qt.MouseButton.LeftButton:
            self.fullscreenExitRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handle wheel events for navigation."""
        delta = event.angleDelta()
        step = delta.y() or delta.x()
        if step < 0:
            self.nextItemRequested.emit()
        elif step > 0:
            self.prevItemRequested.emit()
        event.accept()

    def showEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        """Force position update when widget becomes visible."""
        super().showEvent(event)
        self._update_bar_geometry()

    def hideEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        super().hideEvent(event)
        self.hide_controls(animate=False)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # pragma: no cover - GUI behaviour
        if event.type() in {
            QEvent.Type.MouseMove,
            QEvent.Type.HoverMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
        }:
            self._on_mouse_activity()

        if watched is self._player_bar and event.type() == QEvent.Type.Leave:
            cursor_pos = QCursor.pos()
            if not self.rect().contains(self.mapFromGlobal(cursor_pos)):
                self.hide_controls()

        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _install_activity_filters(self) -> None:
        self._player_bar.installEventFilter(self)

    def _wire_player_bar(self) -> None:
        for signal in (
            self._player_bar.playPauseRequested,
            self._player_bar.scrubStarted,
            self._player_bar.scrubFinished,
        ):
            signal.connect(self._on_mouse_activity)
        self._player_bar.seekRequested.connect(lambda _value: self._on_mouse_activity())
        self._player_bar.volumeChanged.connect(self._on_volume_changed)
        self._player_bar.muteToggled.connect(self._on_mute_toggled)

    def _wire_edit_viewer(self) -> None:
        """Forward image-viewer style signals from the adjusted preview surface."""

        self._edit_viewer.zoomChanged.connect(self.zoomChanged.emit)
        self._edit_viewer.cropChanged.connect(self.cropChanged.emit)
        self._edit_viewer.cropInteractionStarted.connect(self.cropInteractionStarted.emit)
        self._edit_viewer.cropInteractionFinished.connect(self.cropInteractionFinished.emit)
        self._edit_viewer.colorPicked.connect(self.colorPicked.emit)
        self._edit_viewer.firstFrameReady.connect(self.firstFrameReady.emit)
        self._renderer.firstFrameReady.connect(self.firstFrameReady.emit)

    def _on_mouse_activity(self) -> None:
        if not self._controls_enabled:
            return
        self.mouseActive.emit()
        if self._controls_visible:
            self._restart_hide_timer()
        else:
            self.show_controls()

    def _restart_hide_timer(self) -> None:
        if self.player_bar.is_scrubbing():
            self._hide_timer.stop()
        elif self._controls_visible:
            self._hide_timer.start(PLAYER_CONTROLS_HIDE_DELAY_MS)

    def video_view(self) -> QWidget:
        """Return the currently active video surface for focus/event handling."""

        return self._edit_viewer if self._adjusted_preview_enabled else self._renderer

    def video_viewport(self) -> QWidget:
        """Return the widget that accepts keyboard focus."""

        return self.video_view()

    def _animate_to(self, value: float, duration: int) -> None:
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._current_opacity())
        self._fade_anim.setEndValue(value)
        self._fade_anim.setDuration(max(0, duration))
        self._target_opacity = value
        if duration > 0:
            self._fade_anim.start()
        else:
            self._set_opacity(value)
            self._on_fade_finished()

    def _current_opacity(self) -> float:
        effect = self._player_bar.graphicsEffect()
        return effect.opacity() if isinstance(effect, QGraphicsOpacityEffect) else 1.0

    def _set_opacity(self, value: float) -> None:
        effect = self._player_bar.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(max(0.0, min(1.0, value)))

    def _on_fade_finished(self) -> None:
        if self._target_opacity <= 0.0:
            self._player_bar.hide()

    def _update_bar_geometry(self) -> None:
        if not self.isVisible():
            return
        rect = self.rect()
        available_width = max(0, rect.width() - (2 * self._overlay_margin))
        bar_hint = self._player_bar.sizeHint()
        bar_width = min(bar_hint.width(), available_width)
        bar_height = bar_hint.height()
        x = (rect.width() - bar_width) // 2
        y = rect.height() - bar_height - self._overlay_margin
        if y < self._overlay_margin:
            y = max(0, rect.height() - bar_height)
        self._player_bar.setGeometry(x, y, bar_width, bar_height)
        self._player_bar.raise_()

    # ------------------------------------------------------------------
    # Live Photo helpers
    # ------------------------------------------------------------------
    def set_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable the floating playback controls."""

        if self._controls_enabled == enabled:
            return

        self._controls_enabled = enabled
        self._player_bar.setEnabled(enabled)
        self._hide_timer.stop()

        if not enabled:
            # Collapse the chrome immediately so Live Photos play without any
            # overlays, mirroring the legacy image viewer's behaviour.
            self._controls_visible = False
            self._target_opacity = 0.0
            self.hide_controls(animate=False)
            effect = self._player_bar.graphicsEffect()
            if isinstance(effect, QGraphicsOpacityEffect):
                effect.setOpacity(0.0)
        else:
            # Reset fade bookkeeping so the next activity pulse can reveal the
            # controls smoothly from a known baseline.
            self._controls_visible = False
            self._target_opacity = 0.0
            effect = self._player_bar.graphicsEffect()
            if isinstance(effect, QGraphicsOpacityEffect):
                effect.setOpacity(0.0)
            self._player_bar.hide()

    def controls_enabled(self) -> bool:
        """Return ``True`` when the playback controls are currently enabled."""

        return self._controls_enabled
