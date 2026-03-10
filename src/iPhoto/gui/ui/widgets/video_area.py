"""Widget combining the video surface and floating playback controls."""

from __future__ import annotations

import logging
from typing import Optional, cast

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QPointF,
    QRectF,
    QSizeF,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QMouseEvent, QPainter, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

try:  # pragma: no cover - optional Qt module
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
except (ModuleNotFoundError, ImportError):  # pragma: no cover - handled by main window guard
    QGraphicsVideoItem = None  # type: ignore[assignment, misc]
    QMediaPlayer = None
    QAudioOutput = None

from pathlib import Path
from PySide6.QtCore import QUrl

from ....config import (
    PLAYER_CONTROLS_HIDE_DELAY_MS,
    PLAYER_FADE_IN_MS,
    PLAYER_FADE_OUT_MS,
)
from .player_bar import PlayerBar
from ..palette import viewer_surface_color

_log = logging.getLogger(__name__)


def _parse_sar(value: object) -> tuple[int, int] | None:
    """Parse a ``sample_aspect_ratio`` string like ``"16:11"`` or ``"1:1"``."""

    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        num, den = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if num <= 0 or den <= 0:
        return None
    return num, den


def _parse_rotation(value: object) -> int:
    """Parse a rotation value that may be int, float, or string.

    ffprobe may report rotation as ``-90``, ``-90.0``, or ``"-90"``.
    Returns the integer rotation in degrees, or ``0`` on failure.
    """
    if value is None:
        return 0
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


def _probe_display_size(path: Path) -> QSizeF | None:
    """Return the SAR/rotation-corrected display size using ffprobe.

    ``nativeSize()`` on ``QGraphicsVideoItem`` sometimes reports the coded
    resolution rather than the display resolution.  This happens when the
    video has a non-square Sample Aspect Ratio (SAR) or a display-matrix
    rotation.  This helper reads the SAR and rotation from ffprobe and
    applies the corrections so the video item bounding box matches the
    **post-rotation** display aspect ratio.

    Qt's ``QGraphicsVideoItem`` with ``KeepAspectRatio`` renders the
    post-rotation content inside its bounding box.  If the bounding box
    has the wrong aspect ratio, the content is letterboxed *inside* the
    item with black bars.  To avoid this, the item size must match the
    actual display aspect ratio including rotation.

    Returns ``None`` when ffprobe is unavailable or the stream cannot be
    inspected, in which case the caller should fall back to ``nativeSize()``.
    """

    try:
        from ....utils.ffmpeg import probe_media
        meta = probe_media(path)
    except Exception:
        _log.debug("[VideoDbg] ffprobe unavailable for %s", path.name)
        return None

    streams = meta.get("streams", []) if isinstance(meta, dict) else []
    if not isinstance(streams, list):
        return None

    for stream in streams:
        if not isinstance(stream, dict):
            continue
        if stream.get("codec_type") != "video":
            continue

        # Log the raw ffprobe stream metadata for diagnostics.
        _log.info(
            "[VideoDbg] ffprobe stream for %s: codec=%s  coded=%sx%s  "
            "sar=%s  dar=%s  side_data_list=%s  tags.rotate=%s",
            path.name,
            stream.get("codec_name"),
            stream.get("width"), stream.get("height"),
            stream.get("sample_aspect_ratio", "N/A"),
            stream.get("display_aspect_ratio", "N/A"),
            stream.get("side_data_list", "N/A"),
            (stream.get("tags") or {}).get("rotate", "N/A"),
        )

        coded_w = stream.get("width")
        coded_h = stream.get("height")
        if not isinstance(coded_w, int) or not isinstance(coded_h, int):
            continue
        if coded_w <= 0 or coded_h <= 0:
            continue

        display_w = float(coded_w)
        display_h = float(coded_h)

        # Apply frame cropping from side_data_list before SAR/rotation.
        # e.g. {'side_data_type': 'Frame Cropping', 'crop_left': 88,
        #        'crop_right': 88, 'crop_top': 66, 'crop_bottom': 66}
        rotation = 0
        side_data_list = stream.get("side_data_list")
        if isinstance(side_data_list, list):
            for entry in side_data_list:
                if not isinstance(entry, dict):
                    continue
                if entry.get("side_data_type") == "Frame Cropping":
                    crop_l = entry.get("crop_left", 0) or 0
                    crop_r = entry.get("crop_right", 0) or 0
                    crop_t = entry.get("crop_top", 0) or 0
                    crop_b = entry.get("crop_bottom", 0) or 0
                    cropped_w = display_w - crop_l - crop_r
                    cropped_h = display_h - crop_t - crop_b
                    if cropped_w > 0 and cropped_h > 0:
                        _log.info(
                            "[VideoDbg] Frame cropping: l=%d r=%d t=%d b=%d  "
                            "%.0fx%.0f -> %.0fx%.0f",
                            crop_l, crop_r, crop_t, crop_b,
                            display_w, display_h, cropped_w, cropped_h,
                        )
                        display_w = cropped_w
                        display_h = cropped_h
                if "rotation" in entry and rotation == 0:
                    rotation = _parse_rotation(entry["rotation"])

        # Apply Sample Aspect Ratio correction.  A SAR of 16:11 means each
        # coded pixel is 16/11 times as wide as it is tall, so the display
        # width is coded_width * sar_num / sar_den.
        sar = _parse_sar(stream.get("sample_aspect_ratio"))
        if sar is not None and sar != (1, 1):
            sar_num, sar_den = sar
            display_w = display_w * sar_num / sar_den
            _log.info("[VideoDbg] SAR correction: %d:%d  display_w -> %.0f", sar_num, sar_den, display_w)

        # Fall back to stream-level rotation tag (older ffprobe / QuickTime).
        if rotation == 0:
            tags = stream.get("tags")
            if isinstance(tags, dict):
                rotation = _parse_rotation(tags.get("rotate"))

        # 90° and 270° rotations swap width and height.
        abs_rotation = abs(rotation) % 360
        swapped = abs_rotation in (90, 270)
        if swapped:
            display_w, display_h = display_h, display_w

        _log.info(
            "[VideoDbg] _probe_display_size result: %s  coded=%dx%d  "
            "rotation=%d (abs%%360=%d, swapped=%s)  display=%.0fx%.0f",
            path.name, coded_w, coded_h,
            rotation, abs_rotation, swapped, display_w, display_h,
        )
        return QSizeF(display_w, display_h)

    _log.warning("[VideoDbg] No video stream found in %s", path.name)
    return None


class VideoArea(QWidget):
    """Present a video surface with auto-hiding playback controls."""

    mouseActive = Signal()
    controlsVisibleChanged = Signal(bool)
    fullscreenExitRequested = Signal()
    playbackStateChanged = Signal(bool)
    playbackFinished = Signal()
    nextItemRequested = Signal()
    prevItemRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)

        if QGraphicsVideoItem is None:
            raise RuntimeError("PySide6.QtMultimediaWidgets is required for video playback.")

        # --- Graphics View Setup ---
        # A black rectangle sits directly behind the video surface so that
        # hardware compositing sees a black backing.  Some codecs (HDR / HEVC)
        # produce washed-out colours when the compositing backdrop is non-black.
        # The backing rect is sized to match the video item exactly, while the
        # theme-coloured scene background fills the remaining letterbox areas.
        self._black_backing: QGraphicsRectItem = QGraphicsRectItem()
        self._black_backing.setBrush(Qt.black)
        self._black_backing.setPen(Qt.NoPen)
        self._black_backing.setZValue(0)

        self._video_item = QGraphicsVideoItem()
        self._video_item.setZValue(1)
        self._video_item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self._video_item.nativeSizeChanged.connect(self._fit_video_item)

        self._scene = QGraphicsScene(self)
        self._scene.addItem(self._black_backing)
        self._scene.addItem(self._video_item)

        self._video_view = QGraphicsView(self._scene, self)
        self._video_view.setFrameShape(QFrame.Shape.NoFrame)
        self._video_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Accept focus so keyboard navigation targets the video viewport without requiring the user
        # to click a non-interactive chrome element first.
        self._video_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self._video_view)
        # Mirror the palette-driven detail background so letterboxed frames sit
        # on the same neutral backdrop as the surrounding chrome.  The theme
        # colour is used for the scene background (filling letterbox areas),
        # while the black backing rect ensures correct HDR/HEVC compositing
        # directly behind the video content.
        surface_color = viewer_surface_color(self)
        self._default_surface_color = surface_color
        self._surface_override: str | None = None
        self._video_view.setStyleSheet("background: transparent; border: none;")
        self._video_view.viewport().setStyleSheet(
            f"background-color: {surface_color}; border: none;"
        )
        self.setStyleSheet(f"background-color: {surface_color};")
        self._scene.setBackgroundBrush(QColor(surface_color))

        # Display size obtained from ffprobe (SAR + rotation corrected).
        # Preferred over nativeSize() which can report coded dimensions.
        self._display_size: QSizeF | None = None
        # --- End Graphics View Setup ---

        # --- Media Player Setup ---
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_item)

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
        self._host_widget: QWidget | None = self._video_view
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def video_item(self) -> QGraphicsVideoItem:
        """Return the embedded :class:`QGraphicsVideoItem` for media output."""

        return self._video_item

    @property
    def player_bar(self) -> PlayerBar:
        """Return the floating :class:`PlayerBar`."""

        return self._player_bar

    def set_surface_color_override(self, colour: str | None) -> None:
        """Override the viewer backdrop with *colour* or restore the default."""

        self._surface_override = colour
        target = colour if colour is not None else self._default_surface_color
        stylesheet = f"background-color: {target}; border: none;"
        self.setStyleSheet(stylesheet)
        self._video_view.setStyleSheet("background: transparent; border: none;")
        self._video_view.viewport().setStyleSheet(stylesheet)
        self._scene.setBackgroundBrush(QColor(target))

    def set_immersive_background(self, immersive: bool) -> None:
        """Switch to a pure black canvas when immersive full screen mode is active."""

        self.set_surface_color_override("#000000" if immersive else None)

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

    def load_video(self, path: Path) -> None:
        """Load a video file for playback."""
        _log.info("[VideoDbg] load_video: %s", path.name)
        self._display_size = _probe_display_size(path)
        _log.info("[VideoDbg] display_size from probe: %s", self._display_size)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        # Do not auto-play; let the coordinator decide.
        # But ensure we are at start
        self._player.setPosition(0)

    def play(self) -> None:
        """Start or resume playback."""
        self._player.play()

    def pause(self) -> None:
        """Pause playback."""
        self._player.pause()

    def seek(self, position: int) -> None:
        """Seek to a specific position in milliseconds."""
        self._player.setPosition(position)

    def stop(self) -> None:
        """Stop playback and reset."""
        self._player.stop()

    def _on_position_changed(self, position: int) -> None:
        self._player_bar.set_position(position)

    def _on_duration_changed(self, duration: int) -> None:
        self._player_bar.set_duration(duration)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        is_playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self._player_bar.set_playback_state(is_playing)
        self.playbackStateChanged.emit(is_playing)
        if not is_playing and state == QMediaPlayer.PlaybackState.StoppedState:
            self.show_controls()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            duration = self._player.duration()
            if duration <= 0:
                return
            position = self._player.position()
            if position + 200 < duration:
                return
            # Pause and seek to the end so the last frame remains visible instead
            # of the surface going black when the player transitions to StoppedState.
            self._player.pause()
            self._player.setPosition(duration)
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
        self._video_view.setGeometry(rect)
        self._scene.setSceneRect(QRectF(rect))
        self._fit_video_item()
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

    def video_view(self) -> QGraphicsView:
        """Return the graphics view hosting the video surface."""

        # Exposing the graphics view allows higher-level widgets to install
        # shortcut filters directly on the focus target instead of reaching into
        # private attributes.  The method keeps the detail view wiring explicit
        # while still encapsulating the scene graph setup within ``VideoArea``.
        return self._video_view

    def video_viewport(self) -> QWidget:
        """Return the viewport widget that accepts keyboard focus."""

        # Keyboard shortcuts are intercepted at the viewport level so the main
        # window can consume navigation keys before Qt treats them as focus
        # traversal requests.  Providing the widget through a helper keeps the
        # shortcut configuration readable from the controller layer.
        return self._video_view.viewport()

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

    def _fit_video_item(self) -> None:
        """Size and position the video item to match the video's display aspect ratio.

        When the display size is known (from ffprobe SAR + rotation correction
        or from ``nativeSize()`` as a fallback) the video item is shrunk to the
        largest aspect-ratio-preserving rectangle that fits inside the scene.
        The black backing rectangle is sized to the *same* bounds so it sits
        exactly behind the video surface, ensuring correct HDR/HEVC compositing
        without black bars leaking into the theme-coloured letterbox area.

        The display size must account for rotation because Qt renders the
        **post-rotation** content inside the item bounding box.  If the box
        has the wrong aspect ratio, the content is letterboxed with black bars.
        """

        scene_rect = self._scene.sceneRect()
        if scene_rect.isEmpty():
            return

        # Prefer ffprobe-derived display dimensions (SAR + rotation corrected)
        # over nativeSize() which may report coded (uncorrected) dimensions.
        effective_size = self._display_size
        native_size = self._video_item.nativeSize()
        source = "probe"
        if effective_size is None or effective_size.isEmpty():
            effective_size = native_size
            source = "nativeSize"
        if effective_size.isEmpty():
            # No video loaded yet – fill the entire scene so the item is ready
            # to display the first frame without a layout jump.
            self._video_item.setSize(scene_rect.size())
            self._video_item.setPos(QPointF())
            self._black_backing.setPos(QPointF())
            self._black_backing.setRect(QRectF())
            return

        # Compute the largest rectangle inside the scene that preserves the
        # video's display aspect ratio.
        fitted = effective_size.scaled(scene_rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (scene_rect.width() - fitted.width()) / 2.0
        y = (scene_rect.height() - fitted.height()) / 2.0

        _log.info(
            "[VideoDbg] _fit_video_item: source=%s  nativeSize=%.0fx%.0f  "
            "probe_display=%.0fx%.0f  effective=%.0fx%.0f  "
            "scene=%.0fx%.0f  fitted=%.0fx%.0f  pos=(%.0f,%.0f)",
            source,
            native_size.width(), native_size.height(),
            (self._display_size.width() if self._display_size else 0),
            (self._display_size.height() if self._display_size else 0),
            effective_size.width(), effective_size.height(),
            scene_rect.width(), scene_rect.height(),
            fitted.width(), fitted.height(),
            x, y,
        )

        self._video_item.setSize(fitted)
        self._video_item.setPos(QPointF(x, y))

        # Mirror the video item's bounds exactly – trivially aligned.
        self._black_backing.setPos(QPointF(x, y))
        self._black_backing.setRect(QRectF(QPointF(), fitted))

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
