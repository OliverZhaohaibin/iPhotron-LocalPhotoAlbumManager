"""Widget combining the video surface and floating playback controls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _VideoGeometry:
    """Geometry metadata required to size/crop the video item correctly."""

    display_size: QSizeF
    coded_size: QSizeF
    rotation: int
    crop_left: int = 0
    crop_right: int = 0
    crop_top: int = 0
    crop_bottom: int = 0
    coded_crop_left: int = 0
    coded_crop_right: int = 0
    coded_crop_top: int = 0
    coded_crop_bottom: int = 0

    @property
    def has_crop(self) -> bool:
        return any((self.crop_left, self.crop_right, self.crop_top, self.crop_bottom))


def _rotate_crop_to_display(crop: tuple[int, int, int, int], rotation: int) -> tuple[int, int, int, int]:
    """Map coded-space crop margins (l/r/t/b) to display-space margins."""

    l, r, t, b = crop
    rot = rotation % 360
    if rot == 0:
        return l, r, t, b
    if rot == 90:  # 90° CCW
        return b, t, l, r
    if rot == 180:
        return r, l, b, t
    if rot == 270:  # 90° CW
        return t, b, r, l
    return l, r, t, b


def _aspect_ratio(size: QSizeF) -> float:
    """Return width/height while guarding against empty sizes."""

    h = size.height()
    if h <= 0:
        return 0.0
    return size.width() / h


def _fit_area(content: QSizeF, container: QSizeF) -> float:
    """Return fitted area using KeepAspectRatio."""

    if content.isEmpty() or container.isEmpty():
        return 0.0
    fitted = content.scaled(container, Qt.AspectRatioMode.KeepAspectRatio)
    return fitted.width() * fitted.height()


def _fits_within_scene(size: QSizeF, scene: QRectF, *, epsilon: float = 0.5) -> bool:
    """Return True when *size* is fully contained by *scene* (with tolerance)."""

    return size.width() <= scene.width() + epsilon and size.height() <= scene.height() + epsilon

def _probe_display_size(path: Path) -> _VideoGeometry | None:
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
        crop_left = 0
        crop_right = 0
        crop_top = 0
        crop_bottom = 0
        side_data_list = stream.get("side_data_list")
        if isinstance(side_data_list, list):
            for entry in side_data_list:
                if not isinstance(entry, dict):
                    continue
                if entry.get("side_data_type") == "Frame Cropping":
                    crop_l = int(entry.get("crop_left", 0) or 0)
                    crop_r = int(entry.get("crop_right", 0) or 0)
                    crop_t = int(entry.get("crop_top", 0) or 0)
                    crop_b = int(entry.get("crop_bottom", 0) or 0)
                    cropped_w = display_w - crop_l - crop_r
                    cropped_h = display_h - crop_t - crop_b
                    if cropped_w > 0 and cropped_h > 0:
                        crop_left = crop_l
                        crop_right = crop_r
                        crop_top = crop_t
                        crop_bottom = crop_b
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

        crop_display_l, crop_display_r, crop_display_t, crop_display_b = _rotate_crop_to_display(
            (crop_left, crop_right, crop_top, crop_bottom),
            rotation,
        )

        _log.info(
            "[VideoDbg] _probe_display_size result: %s  coded=%dx%d  "
            "rotation=%d (abs%%360=%d, swapped=%s)  display=%.0fx%.0f",
            path.name, coded_w, coded_h,
            rotation, abs_rotation, swapped, display_w, display_h,
        )
        return _VideoGeometry(
            display_size=QSizeF(display_w, display_h),
            coded_size=QSizeF(coded_w, coded_h),
            rotation=rotation,
            crop_left=crop_display_l,
            crop_right=crop_display_r,
            crop_top=crop_display_t,
            crop_bottom=crop_display_b,
            coded_crop_left=crop_left,
            coded_crop_right=crop_right,
            coded_crop_top=crop_top,
            coded_crop_bottom=crop_bottom,
        )

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
        # Avoid edge blending artifacts at the video bounds (visible as thin dark
        # fringes in light mode on some GPU backends).
        self._video_view.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._video_view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
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
        self._video_geometry: _VideoGeometry | None = None
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
        self._video_geometry = _probe_display_size(path)
        _log.info("[VideoDbg] display_size from probe: %s", self._video_geometry)
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
        effective_size = self._video_geometry.display_size if self._video_geometry else None
        native_size = self._video_item.nativeSize()
        source = "probe"
        apply_crop_compensation = bool(self._video_geometry and self._video_geometry.has_crop)
        crop_left = self._video_geometry.crop_left if self._video_geometry else 0
        crop_right = self._video_geometry.crop_right if self._video_geometry else 0
        crop_top = self._video_geometry.crop_top if self._video_geometry else 0
        crop_bottom = self._video_geometry.crop_bottom if self._video_geometry else 0
        if effective_size is None or effective_size.isEmpty():
            effective_size = native_size
            source = "nativeSize"

        # Keep probe-derived orientation authoritative. Some backends report
        # nativeSize() in coded orientation (landscape for iPhone portrait clips),
        # but forcing layout to that orientation causes visibly wrong playback.
        # We still log the candidate comparison for diagnostics.
        if (
            self._video_geometry
            and not native_size.isEmpty()
            and abs(self._video_geometry.rotation) % 360 in (90, 270)
        ):
            rotated = self._video_geometry.display_size
            unrotated = QSizeF(rotated.height(), rotated.width())
            native_aspect = _aspect_ratio(native_size)
            rotated_delta = abs(_aspect_ratio(rotated) - native_aspect)
            unrotated_delta = abs(_aspect_ratio(unrotated) - native_aspect)
            scene_size = scene_rect.size()
            rotated_fill = _fit_area(rotated, scene_size)
            unrotated_fill = _fit_area(unrotated, scene_size)
            _log.info(
                "[VideoDbg] orientation candidates (probe-kept): native=%.0fx%.0f(%.4f) "
                "rotated=%.0fx%.0f(%.4f,Δ=%.4f,fill=%.0f) "
                "unrotated=%.0fx%.0f(%.4f,Δ=%.4f,fill=%.0f)",
                native_size.width(),
                native_size.height(),
                native_aspect,
                rotated.width(),
                rotated.height(),
                _aspect_ratio(rotated),
                rotated_delta,
                rotated_fill,
                unrotated.width(),
                unrotated.height(),
                _aspect_ratio(unrotated),
                unrotated_delta,
                unrotated_fill,
            )
        if effective_size.isEmpty():
            # No video loaded yet – fill the entire scene so the item is ready
            # to display the first frame without a layout jump.
            self._video_item.setSize(scene_rect.size())
            self._video_item.setPos(QPointF())
            self._black_backing.setPos(QPointF())
            self._black_backing.setRect(QRectF())
            return

        # Compute target rectangle. Default to best-fit (no overflow). Only use
        # edge-to-edge expanding for cropped portrait assets when scene/video
        # aspect ratios are already close; otherwise expanding can over-zoom.
        scene_aspect = _aspect_ratio(scene_rect.size())
        effective_aspect = _aspect_ratio(effective_size)
        aspect_gap = abs(scene_aspect - effective_aspect)
        edge_to_edge_fill = bool(
            self._video_geometry
            and self._video_geometry.has_crop
            and abs(self._video_geometry.rotation) % 360 in (90, 270)
            and aspect_gap <= 0.20
        )
        aspect_mode = (
            Qt.AspectRatioMode.KeepAspectRatioByExpanding
            if edge_to_edge_fill
            else Qt.AspectRatioMode.KeepAspectRatio
        )
        fitted = effective_size.scaled(scene_rect.size(), aspect_mode)
        x = (scene_rect.width() - fitted.width()) / 2.0
        y = (scene_rect.height() - fitted.height()) / 2.0

        _log.info(
            "[VideoDbg] _fit_video_item: source=%s  nativeSize=%.0fx%.0f  "
            "probe_display=%.0fx%.0f  effective=%.0fx%.0f  "
            "scene=%.0fx%.0f  fitted=%.0fx%.0f  pos=(%.0f,%.0f)  mode=%s  aspect_gap=%.3f",
            source,
            native_size.width(), native_size.height(),
            (self._video_geometry.display_size.width() if self._video_geometry else 0),
            (self._video_geometry.display_size.height() if self._video_geometry else 0),
            effective_size.width(), effective_size.height(),
            scene_rect.width(), scene_rect.height(),
            fitted.width(), fitted.height(),
            x, y,
            ("expand" if edge_to_edge_fill else "fit"),
            aspect_gap,
        )

        video_size = QSizeF(fitted)
        video_x = x
        video_y = y

        if apply_crop_compensation and self._video_geometry:
            # Crop margins must be normalized in the same coordinate system as
            # ``effective_size``. This keeps compensation correct when we switch
            # to native-orientation fallback (using coded-space crop values).
            visible_w = max(1.0, effective_size.width())
            visible_h = max(1.0, effective_size.height())

            scale_x = (visible_w + crop_left + crop_right) / visible_w
            scale_y = (visible_h + crop_top + crop_bottom) / visible_h
            video_size = QSizeF(fitted.width() * scale_x, fitted.height() * scale_y)
            video_x = x - (fitted.width() * crop_left / visible_w)
            video_y = y - (fitted.height() * crop_top / visible_h)

            # Some Qt multimedia backends still leak a 1px dark fringe along one
            # or more edges after crop compensation because texture sampling lands
            # exactly on boundary texels. Apply a tiny overscan only when crop
            # metadata is present to hide those fringes without changing the global
            # light-mode background.
            overscan_px = 1.0
            video_x -= overscan_px
            video_y -= overscan_px
            video_size = QSizeF(video_size.width() + (2.0 * overscan_px), video_size.height() + (2.0 * overscan_px))

            _log.info(
                "[VideoDbg] applying crop compensation: display_crop l=%d r=%d t=%d b=%d  "
                "video_size=%.0fx%.0f  video_pos=(%.0f,%.0f)  overscan=%.1fpx",
                crop_left,
                crop_right,
                crop_top,
                crop_bottom,
                video_size.width(),
                video_size.height(),
                video_x,
                video_y,
                overscan_px,
            )
            _log.info(
                "[VideoDbg] coverage check: within_scene=%s (scene=%.0fx%.0f, video=%.0fx%.0f)",
                _fits_within_scene(video_size, scene_rect),
                scene_rect.width(),
                scene_rect.height(),
                video_size.width(),
                video_size.height(),
            )

        _log.info(
            "[VideoDbg] final geometry: source=%s effective=%.0fx%.0f crop=(l=%d r=%d t=%d b=%d) "
            "fitted=%.2fx%.2f@(%.2f,%.2f) video_item=%.2fx%.2f@(%.2f,%.2f) scene=%.0fx%.0f mode=%s aspect_gap=%.3f",
            source,
            effective_size.width(),
            effective_size.height(),
            crop_left,
            crop_right,
            crop_top,
            crop_bottom,
            fitted.width(),
            fitted.height(),
            x,
            y,
            video_size.width(),
            video_size.height(),
            video_x,
            video_y,
            scene_rect.width(),
            scene_rect.height(),
            ("expand" if edge_to_edge_fill else "fit"),
            aspect_gap,
        )

        self._video_item.setSize(video_size)
        self._video_item.setPos(QPointF(video_x, video_y))

        # Keep backing behind the visible video viewport in fill mode to avoid
        # exposing any contrasting seam at the scene edge.
        if edge_to_edge_fill:
            self._black_backing.setPos(scene_rect.topLeft())
            self._black_backing.setRect(QRectF(QPointF(), scene_rect.size()))
        else:
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
