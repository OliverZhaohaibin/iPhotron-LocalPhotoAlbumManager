"""Widget combining the video surface and floating playback controls."""

from __future__ import annotations

import logging
import os
import sys
import time
import weakref
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
    QKeyEvent,
    QMouseEvent,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QStackedWidget,
    QWidget,
)

from ....config import (
    PLAYER_CONTROLS_HIDE_DELAY_MS,
    PLAYER_FADE_IN_MS,
    PLAYER_FADE_OUT_MS,
    VIDEO_COMPLETE_HOLD_BACKSTEP_MS,
)
from ....gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailRenderTransaction,
    VideoPresentationState,
)
from ....gui.detail_profile import emit_detail_event, log_detail_profile
from ..palette import viewer_surface_color
from .gl_image_viewer import GLImageViewer
from .video_renderer_widget import VideoRendererWidget, _resolve_frame_rotation_cw

# QtMultimedia is deliberately imported by ``complete_runtime()``.  The staged
# startup path must be able to establish the final QRhiWidget hierarchy without
# also starting the platform media backend before the main window is shown.
QMediaPlayer = None
QAudioOutput = None
QVideoFrame = None  # type: ignore[assignment, misc]
QVideoSink = None  # type: ignore[assignment, misc]
PlayerBar = None
normalise_video_trim = None
video_requires_adjusted_preview = None

_log = logging.getLogger(__name__)
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


def _startup_hang_diag_enabled() -> bool:
    return os.environ.get("IPHOTO_STARTUP_HANG_DIAG", "").strip().lower() in _TRUE_ENV_VALUES


def _load_qt_multimedia_runtime() -> None:
    """Load optional playback classes without replacing test-provided seams."""

    global QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
    if QMediaPlayer is None or QAudioOutput is None or QVideoSink is None:
        try:
            from PySide6.QtMultimedia import (
                QAudioOutput as _QAudioOutput,
            )
            from PySide6.QtMultimedia import (
                QMediaPlayer as _QMediaPlayer,
            )
            from PySide6.QtMultimedia import (
                QVideoFrame as _QVideoFrame,
            )
            from PySide6.QtMultimedia import (
                QVideoSink as _QVideoSink,
            )
        except (ModuleNotFoundError, ImportError):
            return
        if QMediaPlayer is None:
            QMediaPlayer = _QMediaPlayer
        if QAudioOutput is None:
            QAudioOutput = _QAudioOutput
        if QVideoFrame is None:
            QVideoFrame = _QVideoFrame
        if QVideoSink is None:
            QVideoSink = _QVideoSink


def _load_player_bar_class():
    global PlayerBar
    if PlayerBar is None:
        from .player_bar import PlayerBar as _PlayerBar

        PlayerBar = _PlayerBar
    return PlayerBar


def _load_video_adjustment_helpers() -> None:
    global normalise_video_trim, video_requires_adjusted_preview
    if normalise_video_trim is not None and video_requires_adjusted_preview is not None:
        return
    from ....core.adjustment_mapping import (
        normalise_video_trim as _normalise_video_trim,
    )
    from ....core.adjustment_mapping import (
        video_requires_adjusted_preview as _video_requires_adjusted_preview,
    )

    normalise_video_trim = _normalise_video_trim
    video_requires_adjusted_preview = _video_requires_adjusted_preview


def probe_video_rotation(*args, **kwargs):
    """Lazy compatibility seam for tests and older callers."""

    from ....utils.ffmpeg import probe_video_rotation as _probe_video_rotation

    return _probe_video_rotation(*args, **kwargs)


def get_linux_180_prerotate_hint(*args, **kwargs):
    """Lazy compatibility seam for tests and older callers."""

    from ....utils.ffmpeg import (
        get_linux_180_prerotate_hint as _get_linux_180_prerotate_hint,
    )

    return _get_linux_180_prerotate_hint(*args, **kwargs)


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
    playbackFinished = Signal(int, object, int)
    mediaLoadFailed = Signal(Path, str)
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
    mediaFirstFrameReady = Signal(int)
    displaySizeChanged = Signal(QSizeF)
    SHORTCUT_VOLUME_STEP = 5

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        staged: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Prevent the WA_TranslucentBackground cascade from the main window
        # from making the video surface transparent.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)

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
        self._edit_viewer = GLImageViewer(self._surface_stack, staged=staged)
        self._edit_viewer.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # In detail playback, crop framing is disabled but the crop region fills
        # the canvas (strength=1.0) so that crop-edited videos look the same as
        # non-crop videos (both fill the viewport).  Edit mode uses a lower
        # strength (0.5) for the interactive editing experience.
        self._edit_viewer.set_crop_framing_enabled(False)
        self._edit_viewer.set_crop_center_zoom_strength(1.0)
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
        self._pending_video_frame: QVideoFrame | None = None
        self._pending_video_frame_generation: int | None = None
        self._last_presented_video_frame: QVideoFrame | None = None
        self._video_frame_dispatch_pending = False
        self._video_frame_dispatch_generation: int | None = None
        self._media_generation = 0
        self._end_detection_armed_media_generation: int | None = None
        self._position_changed_handler = None
        self._media_status_changed_handler = None
        self._gpu_video_frame_presented_handler = None
        self._detail_request_generation = 0
        self._presentation_committed = False
        self._awaiting_first_gpu_frame_generation: int | None = None
        self._pending_previous_duration_ms = 0
        self._accept_video_frames = False
        self._video_output_attached = False
        self._video_frame_handler = None
        self._diag_queued_frame_count = 0
        self._diag_presented_frame_count = 0
        self._profile_load_started_at: float | None = None
        self._profile_load_source: Path | None = None
        self._profile_first_frame_logged = False
        self._resize_refit_pending = False
        self._resize_refit_timer = QTimer(self)
        self._resize_refit_timer.setSingleShot(True)
        self._resize_refit_timer.setInterval(16)
        self._resize_refit_timer.timeout.connect(self._flush_resize_adjusted_refit)
        # Per-surface zoom tracking so the slider stays in sync when switching surfaces.
        self._renderer_zoom: float = 1.0
        self._edit_viewer_zoom: float = 1.0

        # Playback-only objects are created after the first main-window paint
        # on the staged startup path.  The QRhi widgets above already have
        # their final parents and must never be reparented during completion.
        self._runtime_ready = False
        self._player = None
        self._audio_output = None
        self._video_sink = None
        self._player_bar = None
        self._fade_anim = None
        self._hide_timer = None
        self._overlay_margin = 48
        self._controls_visible = False
        self._target_opacity = 0.0
        self._host_widget: QWidget | None = self._renderer
        self._window_host: QWidget | None = None
        self._controls_enabled = True

        self._apply_surface(surface_color)
        # --- End Video Renderer Setup ---

        self._wire_edit_viewer()
        self._renderer.nativeSizeChanged.connect(self.displaySizeChanged.emit)
        self._bind_gpu_presentation_generation(self._media_generation)

        if not staged:
            self.complete_runtime()

    def complete_runtime(self) -> None:
        """Create playback services while preserving the prepared surfaces."""

        if self._runtime_ready:
            return

        complete_edit_viewer = getattr(self._edit_viewer, "complete_runtime", None)
        if callable(complete_edit_viewer):
            complete_edit_viewer()

        _load_video_adjustment_helpers()
        _load_qt_multimedia_runtime()
        player_bar_class = _load_player_bar_class()
        if QMediaPlayer is None or QAudioOutput is None or QVideoSink is None:
            raise RuntimeError(
                "PySide6.QtMultimedia is required for video playback."
            )

        # --- Media Player Setup ---
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)

        # Route decoded frames through QVideoSink → our custom renderer.
        self._video_sink = QVideoSink(self)
        self._player.setVideoOutput(self._video_sink)
        self._video_output_attached = True
        self._bind_video_sink_generation(self._media_generation)

        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_error_occurred)
        self._bind_end_detection_generation(self._media_generation)
        # --- End Media Player Setup ---

        self._player_bar = player_bar_class(self)
        self._player_bar.hide()
        self._player_bar.setMouseTracking(True)

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
        self._runtime_ready = True

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

        if self._player_bar is None:
            raise RuntimeError("VideoArea playback runtime has not been completed")
        return self._player_bar

    @property
    def edit_viewer(self) -> GLImageViewer:
        """Expose the GL-based adjusted video preview surface."""

        return self._edit_viewer

    def adjusted_preview_enabled(self) -> bool:
        """Return whether decoded frames are routed through the GL edit viewer."""

        return self._adjusted_preview_enabled

    @staticmethod
    def _should_log_diag_frame(index: int) -> bool:
        """Throttle verbose Linux diagnostics so logs stay readable."""

        return index <= 12 or index % 30 == 0

    def _diag_surface_name(self) -> str:
        """Return the currently visible surface for diagnostics."""

        return "edit" if self._surface_stack.currentWidget() is self._edit_viewer else "renderer"

    def _frame_summary(self, frame: "QVideoFrame") -> str:
        """Return a compact frame summary for diagnostics."""

        if frame is None:
            return "none"
        try:
            fmt = frame.surfaceFormat()
        except Exception:
            return "invalid-format"
        try:
            pixel_format = fmt.pixelFormat()
            pixel_name = getattr(pixel_format, "name", None) or str(pixel_format)
        except Exception:
            pixel_name = "unknown"
        try:
            mirrored = bool(fmt.isMirrored())
        except Exception:
            mirrored = False
        return (
            f"{fmt.frameWidth()}x{fmt.frameHeight()} "
            f"pf={pixel_name} mirrored={mirrored}"
        )

    def set_adjusted_preview_enabled(self, enabled: bool) -> None:
        """Route decoded frames through the adjusted GL preview when *enabled*."""

        target = bool(enabled)
        _log.debug(
            "[trace][video_area] set_adjusted_preview_enabled:request %s",
            {
                "target": target,
                "current": self._adjusted_preview_enabled,
                "surface_before": self._diag_surface_name(),
                "size": (self.width(), self.height()),
                "stack_size": (self._surface_stack.width(), self._surface_stack.height()),
            },
        )
        if self._adjusted_preview_enabled == target:
            _log.debug(
                "[trace][video_area] set_adjusted_preview_enabled:noop %s",
                {
                    "target": target,
                    "surface": self._diag_surface_name(),
                    "size": (self.width(), self.height()),
                },
            )
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
        _log.debug(
            "[trace][video_area] set_adjusted_preview_enabled:applied %s",
            {
                "target": target,
                "surface_after": self._diag_surface_name(),
                "edit_mode_active": self._edit_mode_active,
                "adjusted_first_frame_pending": self._adjusted_first_frame_pending,
                "size": (self.width(), self.height()),
                "stack_size": (self._surface_stack.width(), self._surface_stack.height()),
            },
        )
        # Emit the newly-active surface's zoom so the zoom slider stays in sync.
        active_zoom = self._edit_viewer_zoom if self._adjusted_preview_enabled else self._renderer_zoom
        self.zoomChanged.emit(active_zoom)

    def set_edit_mode_active(self, active: bool) -> None:
        """Mark whether the video area is currently being used inside Edit mode."""

        _log.debug(
            "[trace][video_area] set_edit_mode_active %s",
            {
                "active": bool(active),
                "prev_edit_mode_active": self._edit_mode_active,
                "surface_before": self._diag_surface_name(),
                "adjusted_preview_before": self._adjusted_preview_enabled,
            },
        )
        self._edit_mode_active = bool(active)
        self._edit_viewer.set_crop_framing_enabled(self._edit_mode_active)
        # In detail playback, keep crop framing disabled but use strength=1.0 so
        # the crop region fills the canvas, matching non-crop video behaviour.
        # Edit mode uses strength=0.5 for its interactive partial-zoom UX.
        self._edit_viewer.set_crop_center_zoom_strength(1.0 if not self._edit_mode_active else 0.5)
        if self._edit_mode_active:
            self.set_adjusted_preview_enabled(True)
        _log.debug(
            "[trace][video_area] set_edit_mode_active:applied %s",
            {
                "edit_mode_active": self._edit_mode_active,
                "crop_framing_enabled": self._edit_viewer.crop_framing_enabled(),
                "crop_center_zoom_strength": self._edit_viewer.crop_center_zoom_strength(),
                "surface_after": self._diag_surface_name(),
                "adjusted_preview_after": self._adjusted_preview_enabled,
            },
        )

    def is_edit_mode_active(self) -> bool:
        """Return whether the video area is currently in edit mode."""
        return self._edit_mode_active

    def set_adjustments(self, adjustments: Mapping[str, object] | None = None) -> None:
        """Apply GL adjustments to the adjusted preview surface."""

        self._current_adjustments = dict(adjustments or {})
        self._edit_viewer.set_adjustments(self._current_adjustments)

    def apply_committed_adjustments(
        self,
        adjustments: Mapping[str, object] | None = None,
    ) -> None:
        """Update the current video adjustment state without restarting playback."""

        self._current_adjustments = dict(adjustments or {})
        adjusted_preview = video_requires_adjusted_preview(self._current_adjustments)
        self.set_adjusted_preview_enabled(adjusted_preview)
        self._edit_viewer.set_adjustments(self._current_adjustments)
        rotate90_steps = 0
        if not adjusted_preview:
            rotate90_steps = int(
                float(self._current_adjustments.get("Crop_Rotate90", 0.0))
            ) % 4
        self._renderer.set_user_rotate90_steps(rotate90_steps)
        frame = self._last_presented_video_frame
        if adjusted_preview and frame is not None and frame.isValid():
            self._present_video_frame(frame)
        self._renderer.update()
        self.update()

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
        current_steps = int(float(self._current_adjustments.get("Crop_Rotate90", 0.0))) % 4
        next_steps = (current_steps - 1) % 4
        updates = {"Crop_Rotate90": float(next_steps)}
        next_adjustments = {
            **self._current_adjustments,
            **updates,
        }
        if not video_requires_adjusted_preview(next_adjustments):
            self._current_adjustments = next_adjustments
            if self._adjusted_preview_enabled:
                self.set_adjusted_preview_enabled(False)
            self._renderer.set_user_rotate90_steps(next_steps)
            self._renderer.update()
            self.update()
            return updates

        self.set_adjusted_preview_enabled(True)
        updates = self._edit_viewer.rotate_image_ccw()
        if updates:
            self._current_adjustments = {
                **self._current_adjustments,
                **updates,
            }
        frame = self._last_presented_video_frame
        if frame is not None and frame.isValid():
            self._present_video_frame(frame)
        return updates

    def set_zoom(self, factor: float, anchor: QPointF | None = None) -> None:
        effective_anchor = anchor or self.viewport_center()
        if self._adjusted_preview_enabled:
            self._edit_viewer.set_zoom(factor, anchor=effective_anchor)
        else:
            self._renderer.set_zoom(factor, anchor=effective_anchor)

    def reset_zoom(self) -> None:
        if self._adjusted_preview_enabled:
            self._edit_viewer.reset_zoom()
        else:
            self._renderer.reset_zoom()

    def zoom_in(self) -> None:
        if self._adjusted_preview_enabled:
            self._edit_viewer.zoom_in()
        else:
            self._renderer.zoom_in()

    def zoom_out(self) -> None:
        if self._adjusted_preview_enabled:
            self._edit_viewer.zoom_out()
        else:
            self._renderer.zoom_out()

    def viewport_center(self) -> QPointF:
        if self._adjusted_preview_enabled:
            return self._edit_viewer.viewport_center()
        return self._renderer.viewport_center()

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
        self._surface_stack.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, target)
        self._surface_stack.setAutoFillBackground(not target)
        if target:
            self._surface_stack.setStyleSheet("background: transparent; border: none;")
        else:
            self._surface_stack.setStyleSheet("")
        self._renderer.set_transparent_rounded_clip(corner_radius if target else 0.0)
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

        if not self._runtime_ready or not self._controls_enabled:
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

        if not self._runtime_ready:
            return
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

        if not self._runtime_ready or not self._controls_enabled:
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

    def is_muted(self) -> bool:
        """Return whether the audio output is currently muted."""
        return self._audio_output.isMuted()

    def toggle_mute(self) -> None:
        """Toggle the audio output mute state."""
        self.set_muted(not self.is_muted())

    def has_video(self) -> bool:
        """Return True when a video source is currently loaded."""
        return self._current_source is not None

    def current_source(self) -> Path | None:
        """Return the currently loaded video source, if any."""
        return self._current_source

    def present_video(
        self,
        path: Path,
        *,
        adjustments: Mapping[str, object] | None = None,
        trim_range_ms: tuple[int, int] | None = None,
        adjusted_preview: bool | None = None,
    ) -> None:
        """Present a non-Detail video through the shared transaction contract."""

        generation = self._detail_request_generation + 1
        transaction = DetailRenderTransaction(
            generation=generation,
            asset_id=path.name,
            media_kind="video",
            source_identity=AssetSourceIdentity.create(path),
        )
        self.begin_load(path, generation)
        self.commit_presentation(
            VideoPresentationState(
                request_generation=generation,
                adjustments=dict(adjustments or {}),
                trim_range_ms=trim_range_ms,
                adjusted_preview=bool(adjusted_preview),
                rotation_cw=0,
                raw_width=0,
                raw_height=0,
                linux_180_hint=False,
                transaction=transaction,
            )
        )

    def begin_load(self, path: Path, request_generation: int) -> int:
        """Set the source on the GUI thread and return its media generation."""

        load_started = time.perf_counter()
        prev_source = self._current_source
        previous_duration_ms = self._current_duration_ms
        media_generation = self._begin_media_generation()
        self._detach_video_output()
        if sys.platform == "darwin" and prev_source is not None:
            # AVFoundation can keep the previous audio session alive unless
            # the source is cleared before loading another clip.
            self._player.setSource(QUrl())
        self._bind_video_sink_generation(self._media_generation)
        self._attach_video_output()
        # Do not accept a frame until rotation/trim/adjustment state is known.
        self._accept_video_frames = False
        self._presentation_committed = False
        self._awaiting_first_gpu_frame_generation = None
        self._detail_request_generation = int(request_generation)
        self._profile_load_started_at = load_started
        self._profile_load_source = path
        self._profile_first_frame_logged = False
        self._current_source = path
        self._pending_previous_duration_ms = (
            previous_duration_ms if prev_source == path else 0
        )
        self._current_adjustments = {}
        self.set_adjusted_preview_enabled(False)
        self._edit_viewer.set_adjustments({})
        self._edit_viewer.set_video_source_rotation(0)
        self._renderer.set_user_rotate90_steps(0)
        self._renderer.set_container_rotation(0, 0, 0)
        self._renderer.set_linux_180_hint(False)
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._current_duration_ms = 0
        self._end_hold_display_ms = None

        set_source_started = time.perf_counter()
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        log_detail_profile(
            "video_area",
            "set_source",
            (time.perf_counter() - set_source_started) * 1000.0,
            path=path.name,
        )
        emit_detail_event(
            "video_source_set",
            generation=self._detail_request_generation,
            media_type="video",
            suffix=path.suffix.lower(),
        )
        return media_generation

    def commit_presentation(self, state: VideoPresentationState) -> bool:
        """Apply prepared metadata and begin accepting current video frames."""

        if int(state.generation) != self._detail_request_generation:
            return False
        self._current_adjustments = dict(state.adjustments or {})
        adjusted_preview = bool(
            state.adjusted_preview
            or video_requires_adjusted_preview(self._current_adjustments)
        )
        self.set_adjusted_preview_enabled(adjusted_preview)
        self._adjusted_first_frame_pending = adjusted_preview
        self._edit_viewer.set_adjustments(self._current_adjustments)
        native_rotate90_steps = 0
        if not adjusted_preview:
            native_rotate90_steps = int(
                float(self._current_adjustments.get("Crop_Rotate90", 0.0))
            ) % 4
        self._renderer.set_user_rotate90_steps(native_rotate90_steps)
        self._container_rotation_cw = int(state.rotation_cw) % 360
        self._container_raw_w = int(state.raw_width)
        self._container_raw_h = int(state.raw_height)
        self._container_linux_180_hint = bool(state.linux_180_hint)
        self._renderer.set_container_rotation(
            self._container_rotation_cw,
            self._container_raw_w,
            self._container_raw_h,
        )
        self._renderer.set_linux_180_hint(self._container_linux_180_hint)
        if self._container_raw_w > 0 and self._container_raw_h > 0:
            width, height = self._container_raw_w, self._container_raw_h
            if self._container_rotation_cw in (90, 270):
                width, height = height, width
            self.displaySizeChanged.emit(QSizeF(float(width), float(height)))
        if state.trim_range_ms is not None:
            self.set_trim_range_ms(*state.trim_range_ms)
        duration_ms = self._pending_previous_duration_ms or self._player.duration()
        self._pending_previous_duration_ms = 0
        if duration_ms > 0:
            self._on_duration_changed(duration_ms)
        self._player.setPosition(self._trim_in_ms if self._trim_in_ms > 0 else 0)
        self._accept_video_frames = True
        self._presentation_committed = True
        self._awaiting_first_gpu_frame_generation = self._detail_request_generation
        return True

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

    def current_position(self) -> int:
        """Return the current playback position in milliseconds."""
        return int(self._player.position())

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
        diag_enabled = _startup_hang_diag_enabled()
        started_at = time.perf_counter()
        if diag_enabled:
            _log.info("VideoArea.stop: begin source=%s", self._current_source)

        phase_started = time.perf_counter()
        self._begin_media_generation()
        self._log_stop_phase("frame_quiesce", phase_started, diag_enabled)

        phase_started = time.perf_counter()
        self._detach_video_output()
        self._log_stop_phase("sink_detach", phase_started, diag_enabled)

        # A null source already stops playback and asks the backend to release
        # all media I/O.  Calling player.stop() first is redundant and, on the
        # Windows backend, can deadlock while Python still owns decoded frame
        # wrappers.  All downstream frame references were dropped above.
        phase_started = time.perf_counter()
        self._player.setSource(QUrl())
        self._log_stop_phase("source_clear", phase_started, diag_enabled)

        phase_started = time.perf_counter()
        self._renderer.set_user_rotate90_steps(0)
        self._renderer.set_container_rotation(0, 0, 0)
        self._renderer.set_linux_180_hint(False)
        self._edit_viewer.set_adjustments({})
        self._edit_viewer.set_video_source_rotation(0)
        self._current_adjustments = {}
        self._current_source = None
        self._current_duration_ms = 0
        self._container_rotation_cw = 0
        self._container_raw_w = 0
        self._container_raw_h = 0
        self._container_linux_180_hint = False
        self._adjusted_first_frame_pending = False
        self._profile_load_started_at = None
        self._profile_load_source = None
        self._profile_first_frame_logged = False
        self._presentation_committed = False
        self._awaiting_first_gpu_frame_generation = None
        self._trim_in_ms = 0
        self._trim_out_ms = 0
        self._suppress_trim_pause = False
        self._restart_from_trim_in_on_play = False
        self._end_hold_display_ms = None
        self._log_stop_phase("state_reset", phase_started, diag_enabled)
        if diag_enabled:
            _log.info(
                "VideoArea.stop: end elapsed_ms=%.1f",
                (time.perf_counter() - started_at) * 1000.0,
            )

    def _begin_media_generation(self) -> int:
        """Invalidate queued frames and release every downstream frame owner."""

        self._media_generation += 1
        self._end_detection_armed_media_generation = None
        self._bind_end_detection_generation(self._media_generation)
        self._bind_gpu_presentation_generation(self._media_generation)
        self._accept_video_frames = False
        self._resize_refit_timer.stop()
        self._resize_refit_pending = False
        self._pending_video_frame = None
        self._pending_video_frame_generation = None
        self._last_presented_video_frame = None
        self._video_frame_dispatch_pending = False
        self._video_frame_dispatch_generation = None
        self._renderer.clear_frame()
        self._edit_viewer.clear()
        return self._media_generation

    def _bind_end_detection_generation(self, generation: int) -> None:
        """Bind end-producing player callbacks to one media generation."""

        previous_position_handler = self._position_changed_handler
        if previous_position_handler is not None:
            try:
                self._player.positionChanged.disconnect(previous_position_handler)
            except (RuntimeError, TypeError):
                pass

        previous_status_handler = self._media_status_changed_handler
        if previous_status_handler is not None:
            try:
                self._player.mediaStatusChanged.disconnect(previous_status_handler)
            except (RuntimeError, TypeError):
                pass

        def _handle_position(position: int, *, bound_generation: int = generation) -> None:
            self._on_position_changed(position, media_generation=bound_generation)

        def _handle_status(
            status: QMediaPlayer.MediaStatus,
            *,
            bound_generation: int = generation,
        ) -> None:
            self._on_media_status_changed(status, media_generation=bound_generation)

        self._position_changed_handler = _handle_position
        self._media_status_changed_handler = _handle_status
        self._player.positionChanged.connect(_handle_position)
        self._player.mediaStatusChanged.connect(_handle_status)

    def _end_detection_is_armed(self, media_generation: int) -> bool:
        """Return whether end detection belongs to the current presented media."""

        return (
            int(media_generation) == self._media_generation
            and self._end_detection_armed_media_generation == self._media_generation
        )

    def _bind_gpu_presentation_generation(self, generation: int) -> None:
        """Bind GPU frame completion to the media generation that queued it."""

        previous = self._gpu_video_frame_presented_handler
        if previous is not None:
            for signal in (
                self._renderer.videoFramePresented,
                self._edit_viewer.videoFramePresented,
            ):
                try:
                    signal.disconnect(previous)
                except (RuntimeError, TypeError):
                    pass

        owner_ref = weakref.ref(self)

        def _handle_presented(*, bound_generation: int = generation) -> None:
            owner = owner_ref()
            if owner is not None:
                owner._on_gpu_video_frame_presented(media_generation=bound_generation)

        self._gpu_video_frame_presented_handler = _handle_presented
        self._renderer.videoFramePresented.connect(_handle_presented)
        self._edit_viewer.videoFramePresented.connect(_handle_presented)

    def _bind_video_sink_generation(self, generation: int) -> None:
        """Bind sink delivery to one media generation so old queued calls expire."""

        previous = self._video_frame_handler
        if previous is not None:
            try:
                self._video_sink.videoFrameChanged.disconnect(previous)
            except (RuntimeError, TypeError):
                pass

        def _handle_frame(frame, *, bound_generation: int = generation) -> None:
            self._queue_video_frame(frame, generation=bound_generation)

        self._video_frame_handler = _handle_frame
        self._video_sink.videoFrameChanged.connect(
            _handle_frame,
            Qt.ConnectionType.QueuedConnection,
        )

    def _attach_video_output(self) -> None:
        if self._video_output_attached:
            return
        self._player.setVideoOutput(self._video_sink)
        self._video_output_attached = True

    def _detach_video_output(self) -> None:
        if not self._video_output_attached:
            return
        self._player.setVideoOutput(None)
        self._video_output_attached = False

    @staticmethod
    def _log_stop_phase(name: str, started_at: float, diag_enabled: bool) -> None:
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if diag_enabled:
            _log.info("VideoArea.stop: phase=%s elapsed_ms=%.1f", name, elapsed_ms)
        if elapsed_ms > 100.0:
            _log.warning("VideoArea.stop phase %s took %.1fms", name, elapsed_ms)

    def _queue_video_frame(
        self,
        frame: "QVideoFrame",
        *,
        generation: int | None = None,
    ) -> None:
        """Coalesce video-sink frames back onto the GUI event loop."""

        frame_generation = self._media_generation if generation is None else generation
        if not self._accept_video_frames or frame_generation != self._media_generation:
            return
        if frame is None or not frame.isValid():
            return
        queued_frame = frame
        if QVideoFrame is not None:
            try:
                queued_frame = QVideoFrame(frame)
            except Exception:
                queued_frame = frame
        self._diag_queued_frame_count += 1
        if sys.platform.startswith("linux") and self._should_log_diag_frame(self._diag_queued_frame_count):
            _log.warning(
                "[diag][video_area] queue #%d adjusted=%s surface=%s dispatch_pending=%s frame=%s",
                self._diag_queued_frame_count,
                self._adjusted_preview_enabled,
                self._diag_surface_name(),
                self._video_frame_dispatch_pending,
                self._frame_summary(queued_frame),
            )
        self._pending_video_frame = queued_frame
        self._pending_video_frame_generation = frame_generation
        if (
            self._video_frame_dispatch_pending
            and self._video_frame_dispatch_generation == frame_generation
        ):
            return
        self._video_frame_dispatch_pending = True
        self._video_frame_dispatch_generation = frame_generation
        # Keep latest-frame coalescing by deferring the flush to the next GUI
        # turn. The queued frame wrapper is copied above so Linux backends can
        # still retain short-lived zero-copy handles until presentation.
        QTimer.singleShot(
            0,
            lambda generation=frame_generation: self._flush_pending_video_frame(generation),
        )

    def _flush_pending_video_frame(self, generation: int | None = None) -> None:
        """Present the latest queued frame on the active preview surface."""

        frame_generation = self._media_generation if generation is None else generation
        if (
            frame_generation != self._media_generation
            or not self._accept_video_frames
            or self._video_frame_dispatch_generation != frame_generation
        ):
            return
        self._video_frame_dispatch_pending = False
        self._video_frame_dispatch_generation = None
        frame = self._pending_video_frame
        pending_generation = self._pending_video_frame_generation
        self._pending_video_frame = None
        self._pending_video_frame_generation = None
        if (
            pending_generation != frame_generation
            or frame is None
            or not frame.isValid()
        ):
            return
        if sys.platform.startswith("linux") and self._should_log_diag_frame(self._diag_queued_frame_count):
            _log.warning(
                "[diag][video_area] flush queue_count=%d presented=%d adjusted=%s surface=%s frame=%s",
                self._diag_queued_frame_count,
                self._diag_presented_frame_count,
                self._adjusted_preview_enabled,
                self._diag_surface_name(),
                self._frame_summary(frame),
            )
        self._present_video_frame(frame)

    def _on_video_frame(self, frame: "QVideoFrame") -> None:
        """Test hook that forwards a frame immediately to the preview surface."""

        self._present_video_frame(frame)

    def _present_video_frame(self, frame: "QVideoFrame") -> None:
        """Forward each decoded frame to the active GPU-backed preview surface."""

        if (
            not self._profile_first_frame_logged
            and self._profile_load_started_at is not None
            and self._profile_load_source == self._current_source
            and self._current_source is not None
        ):
            self._profile_first_frame_logged = True
            log_detail_profile(
                "video_area",
                "first_frame",
                (time.perf_counter() - self._profile_load_started_at) * 1000.0,
                path=self._current_source.name,
                adjusted_preview=self._adjusted_preview_enabled,
                surface=self._diag_surface_name(),
            )

        if QVideoFrame is not None:
            try:
                self._last_presented_video_frame = QVideoFrame(frame)
            except Exception:
                self._last_presented_video_frame = frame
        else:
            self._last_presented_video_frame = frame
        self._diag_presented_frame_count += 1
        if self._diag_presented_frame_count <= 12 or self._diag_presented_frame_count % 30 == 0:
            _log.debug(
                "[trace][video_area] present_frame %s",
                {
                    "count": self._diag_presented_frame_count,
                    "surface": self._diag_surface_name(),
                    "adjusted_preview": self._adjusted_preview_enabled,
                    "adjusted_first_frame_pending": self._adjusted_first_frame_pending,
                    "edit_mode_active": self._edit_mode_active,
                    "current_adjustments_keys": sorted(self._current_adjustments.keys()),
                    "size": (self.width(), self.height()),
                    "stack_size": (self._surface_stack.width(), self._surface_stack.height()),
                    "frame": self._frame_summary(frame),
                },
            )
        if self._adjusted_preview_enabled:
            resolved_rotation_cw = _resolve_frame_rotation_cw(
                frame.surfaceFormat(),
                container_rotation_cw=self._container_rotation_cw,
                container_raw_w=self._container_raw_w,
                container_raw_h=self._container_raw_h,
                linux_180_hint=self._container_linux_180_hint,
            )
            if sys.platform.startswith("linux") and self._should_log_diag_frame(self._diag_presented_frame_count):
                _log.warning(
                    "[diag][video_area] present #%d mode=edit surface=%s reset_view=%s rotation=%d frame=%s",
                    self._diag_presented_frame_count,
                    self._diag_surface_name(),
                    self._adjusted_first_frame_pending,
                    resolved_rotation_cw,
                    self._frame_summary(frame),
                )
            self._edit_viewer.set_pending_video_source_rotation(resolved_rotation_cw)
            reset_view = self._adjusted_first_frame_pending
            self._adjusted_first_frame_pending = False
            self._edit_viewer.set_video_frame(
                frame,
                self._current_adjustments,
                reset_view=reset_view,
            )
            self._surface_stack.update()
            self.update()
        else:
            self._renderer.update_frame(frame)

    def _on_gpu_video_frame_presented(
        self,
        *,
        media_generation: int | None = None,
    ) -> None:
        """Release loading UI only after the current frame completed QRhi draw."""

        event_generation = (
            self._media_generation if media_generation is None else int(media_generation)
        )
        if event_generation != self._media_generation:
            return
        generation = self._awaiting_first_gpu_frame_generation
        if generation is None or generation != self._detail_request_generation:
            return
        self._awaiting_first_gpu_frame_generation = None
        self._end_detection_armed_media_generation = event_generation
        self.mediaFirstFrameReady.emit(generation)

        if not self._end_detection_is_armed(event_generation):
            return
        position = self._player.position()
        if self._trim_out_ms > self._trim_in_ms and position >= self._trim_out_ms:
            self._on_position_changed(position, media_generation=event_generation)
            return
        if self._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            self._on_media_status_changed(
                QMediaPlayer.MediaStatus.EndOfMedia,
                media_generation=event_generation,
            )

    def _on_position_changed(
        self,
        position: int,
        *,
        media_generation: int | None = None,
    ) -> None:
        event_generation = (
            self._media_generation if media_generation is None else int(media_generation)
        )
        if event_generation != self._media_generation:
            return
        if (
            self._end_detection_is_armed(event_generation)
            and self._trim_out_ms > self._trim_in_ms
            and position >= self._trim_out_ms
        ):
            self._enter_end_hold(
                end_pos=self._trim_out_ms,
                hold_pos=max(
                    self._trim_in_ms,
                    self._trim_out_ms - VIDEO_COMPLETE_HOLD_BACKSTEP_MS,
                ),
                media_generation=event_generation,
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
        if sys.platform.startswith("linux"):
            _log.warning(
                "[diag][video_area] playback_state=%s adjusted=%s surface=%s pos=%d pending_queue=%s",
                state,
                self._adjusted_preview_enabled,
                self._diag_surface_name(),
                self._player.position(),
                self._video_frame_dispatch_pending,
            )
        if not is_playing and state == QMediaPlayer.PlaybackState.StoppedState:
            self.show_controls()

    def _on_media_status_changed(
        self,
        status: QMediaPlayer.MediaStatus,
        *,
        media_generation: int | None = None,
    ) -> None:
        event_generation = (
            self._media_generation if media_generation is None else int(media_generation)
        )
        if event_generation != self._media_generation:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if not self._end_detection_is_armed(event_generation):
                return
            duration = (
                self._trim_out_ms
                if self._trim_out_ms > self._trim_in_ms
                else self._player.duration()
            )
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
                media_generation=event_generation,
            )

    def _on_error_occurred(self, _error: object, message: str) -> None:
        source = self._current_source
        if source is None:
            return
        detail = message.strip() if isinstance(message, str) and message.strip() else (
            "An unknown media playback error occurred."
        )
        self.mediaLoadFailed.emit(source, detail)

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

    def _enter_end_hold(
        self,
        *,
        end_pos: int,
        hold_pos: int,
        media_generation: int | None = None,
    ) -> None:
        """Pause on the last frame while keeping the timeline cursor at the end."""

        event_generation = (
            self._media_generation if media_generation is None else int(media_generation)
        )
        if not self._end_detection_is_armed(event_generation):
            return
        end_pos = max(0, int(end_pos))
        hold_pos = max(0, min(int(hold_pos), end_pos))
        if self._restart_from_trim_in_on_play and self._end_hold_display_ms == end_pos:
            self._sync_position_display(end_pos)
            return
        if self._suppress_trim_pause:
            return
        finished_request_generation = self._detail_request_generation
        finished_source = self._current_source
        finished_media_generation = event_generation
        self._suppress_trim_pause = True
        self._end_hold_display_ms = end_pos
        self._restart_from_trim_in_on_play = True
        self._player.pause()
        self._player.setPosition(hold_pos)
        self._suppress_trim_pause = False
        self._sync_position_display(end_pos)
        self.show_controls()
        self.playbackFinished.emit(
            finished_request_generation,
            finished_source,
            finished_media_generation,
        )

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
        _log.debug(
            "[trace][video_area] resize %s",
            {
                "area_size": (rect.width(), rect.height()),
                "stack_size": (self._surface_stack.width(), self._surface_stack.height()),
                "surface": self._diag_surface_name(),
                "adjusted_preview": self._adjusted_preview_enabled,
                "edit_mode_active": self._edit_mode_active,
                "player_container_size": (
                    self.parentWidget().width() if self.parentWidget() else None,
                    self.parentWidget().height() if self.parentWidget() else None,
                ),
            },
        )
        if sys.platform.startswith("linux"):
            _log.warning(
                "[diag][video_area] resize area=%dx%d stack=%dx%d surface=%s adjusted=%s",
                rect.width(),
                rect.height(),
                self._surface_stack.width(),
                self._surface_stack.height(),
                self._diag_surface_name(),
                self._adjusted_preview_enabled,
            )
        # Keep adjusted preview framed correctly after layout transitions
        # (especially edit -> detail), where multiple resizes can happen after
        # the first adjusted frame already consumed reset_view=True.
        if self._adjusted_preview_enabled and not self._edit_mode_active:
            frame = self._last_presented_video_frame
            if frame is not None and frame.isValid():
                _log.debug(
                    "[trace][video_area] resize:queue_adjusted_refit %s",
                    {
                        "surface": self._diag_surface_name(),
                        "adjusted_preview": self._adjusted_preview_enabled,
                        "edit_mode_active": self._edit_mode_active,
                        "size": (rect.width(), rect.height()),
                        "stack_size": (
                            self._surface_stack.width(),
                            self._surface_stack.height(),
                        ),
                        "frame": self._frame_summary(frame),
                    },
                )
                self._resize_refit_pending = True
                self._resize_refit_timer.start()

    def _flush_resize_adjusted_refit(self) -> None:
        """Coalesce resize-triggered adjusted-preview refits into one update."""

        if not self._resize_refit_pending:
            return
        self._resize_refit_pending = False
        if not self._adjusted_preview_enabled or self._edit_mode_active:
            return
        frame = self._last_presented_video_frame
        if frame is None or not frame.isValid():
            return
        self._adjusted_first_frame_pending = True
        self._present_video_frame(frame)

    def enterEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        super().enterEvent(event)
        self.show_controls()

    def leaveEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        super().leaveEvent(event)
        if self._player_bar is not None and not self._player_bar.underMouse():
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

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle focus-based playback shortcuts while the video area has focus.

        Only Up/Down (volume) are handled here because they need the video
        widget to have focus to avoid conflicting with list/slider navigation
        elsewhere in the window.  Space, M, and other transport shortcuts are
        handled globally by ``AppShortcutManager``.
        """
        if not self._runtime_ready or self._edit_mode_active:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            current_volume = int(round(self._audio_output.volume() * 100))
            step = self.SHORTCUT_VOLUME_STEP if key == Qt.Key.Key_Up else -self.SHORTCUT_VOLUME_STEP
            self.set_volume(current_volume + step)
            self._on_mouse_activity()
            event.accept()
            return

        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        """Force position update when widget becomes visible."""
        super().showEvent(event)
        self._update_bar_geometry()

    def hideEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        super().hideEvent(event)
        self.hide_controls(animate=False)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # pragma: no cover - GUI behaviour
        if not self._runtime_ready:
            return super().eventFilter(watched, event)
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
        self._player_bar.seekRequested.connect(self._on_seek_activity_requested)
        self._player_bar.volumeChanged.connect(self._on_volume_changed)
        self._player_bar.muteToggled.connect(self._on_mute_toggled)

    def _on_seek_activity_requested(self, _value: int) -> None:
        self._on_mouse_activity()

    def _wire_edit_viewer(self) -> None:
        """Forward image-viewer style signals from the adjusted preview surface."""

        self._edit_viewer.zoomChanged.connect(self._on_edit_viewer_zoom_changed)
        self._renderer.zoomChanged.connect(self._on_renderer_zoom_changed)
        self._edit_viewer.cropChanged.connect(self.cropChanged.emit)
        self._edit_viewer.cropInteractionStarted.connect(self.cropInteractionStarted.emit)
        self._edit_viewer.cropInteractionFinished.connect(self.cropInteractionFinished.emit)
        self._edit_viewer.colorPicked.connect(self.colorPicked.emit)
        self._edit_viewer.firstFrameReady.connect(self.firstFrameReady.emit)
        self._renderer.firstFrameReady.connect(self.firstFrameReady.emit)

    def _on_edit_viewer_zoom_changed(self, factor: float) -> None:
        """Forward zoom changes from _edit_viewer only when it is the active surface."""
        self._edit_viewer_zoom = factor
        if self._adjusted_preview_enabled:
            self.zoomChanged.emit(factor)

    def _on_renderer_zoom_changed(self, factor: float) -> None:
        """Forward zoom changes from _renderer only when it is the active surface."""
        self._renderer_zoom = factor
        if not self._adjusted_preview_enabled:
            self.zoomChanged.emit(factor)

    def _on_mouse_activity(self) -> None:
        if not self._runtime_ready or not self._controls_enabled:
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
        if self._player_bar is None or not self.isVisible():
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
