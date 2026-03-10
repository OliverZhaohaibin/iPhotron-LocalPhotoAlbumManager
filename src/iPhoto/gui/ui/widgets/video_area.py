"""Widget combining the video surface and floating playback controls."""

from __future__ import annotations

import logging
from typing import Optional

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
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QMouseEvent,
    QPainter,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsOpacityEffect,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

try:  # pragma: no cover - optional Qt module
    from PySide6.QtMultimedia import (
        QAudioOutput,
        QMediaPlayer,
        QVideoFrame,
        QVideoFrameFormat,
    )
    from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
except (ModuleNotFoundError, ImportError):  # pragma: no cover - handled by main window guard
    QGraphicsVideoItem = None  # type: ignore[assignment, misc]
    QMediaPlayer = None
    QAudioOutput = None
    QVideoFrame = None  # type: ignore[assignment, misc]
    QVideoFrameFormat = None  # type: ignore[assignment, misc]

from pathlib import Path
from PySide6.QtCore import QUrl

_log = logging.getLogger(__name__)

from ....config import (
    PLAYER_CONTROLS_HIDE_DELAY_MS,
    PLAYER_FADE_IN_MS,
    PLAYER_FADE_OUT_MS,
    VIDEO_COMPLETE_HOLD_BACKSTEP_MS,
)
from .player_bar import PlayerBar
from ..palette import viewer_surface_color


# ---------------------------------------------------------------------------
# HDR detection helpers
# ---------------------------------------------------------------------------

def _is_hdr_frame_format(fmt: "QVideoFrameFormat") -> bool:
    """Return ``True`` when *fmt* indicates HDR / wide-gamut content.

    Checks for BT.2020 colour space or HLG / PQ transfer functions which
    require tone-mapping for correct SDR display.
    """

    if QVideoFrameFormat is None:
        return False
    try:
        if fmt.colorSpace() == QVideoFrameFormat.ColorSpace.ColorSpace_BT2020:
            return True
        if fmt.colorTransfer() in {
            QVideoFrameFormat.ColorTransfer.ColorTransfer_ST2084,
            QVideoFrameFormat.ColorTransfer.ColorTransfer_STD_B67,
        }:
            return True
    except Exception:  # pragma: no cover - defensive
        pass
    return False


# ---------------------------------------------------------------------------
# Custom QGraphicsItem for SDR-converted HDR video frames
# ---------------------------------------------------------------------------

class _SdrVideoItem(QGraphicsItem):
    """Lightweight item that renders SDR-converted video frames.

    When the playback pipeline detects HDR content, :class:`VideoArea` hides
    the standard :class:`QGraphicsVideoItem` and directs each incoming
    :class:`QVideoFrame` through :meth:`QVideoFrame.toImage` — which
    applies Qt's built-in tone-mapping / colour-space conversion — then
    paints the resulting 8-bit sRGB :class:`QImage` via the QPainter path.

    This avoids the compositing artefacts (washed-out / grey colours) that
    occur when the RHI shader for ``QGraphicsVideoItem`` renders HDR textures
    against a non-black scene background.
    """

    def __init__(self, parent: Optional[QGraphicsItem] = None) -> None:
        super().__init__(parent)
        self._image: QImage = QImage()
        self._size: QSizeF = QSizeF()

    # -- geometry ----------------------------------------------------------

    def setSize(self, size: QSizeF) -> None:
        if self._size != size:
            self.prepareGeometryChange()
            self._size = size

    def boundingRect(self) -> QRectF:
        return QRectF(QPointF(), self._size)

    # -- frame update ------------------------------------------------------

    def updateFrame(self, frame: "QVideoFrame") -> None:
        """Convert *frame* to an SDR QImage and schedule a repaint."""

        if not frame.isValid():
            return
        img = frame.toImage()
        if not img.isNull():
            self._image = img
            self.update()

    def clearFrame(self) -> None:
        self._image = QImage()
        self.update()

    # -- painting ----------------------------------------------------------

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        if self._image.isNull() or self._size.isEmpty():
            return
        img_size = QSizeF(self._image.width(), self._image.height())
        scaled = img_size.scaled(self._size, Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(QPointF(), scaled)
        target.moveCenter(QRectF(QPointF(), self._size).center())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(target, self._image)


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
        self._video_item = QGraphicsVideoItem()
        self._video_item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)

        # SDR fallback item for HDR content — hidden until HDR is detected.
        self._sdr_item = _SdrVideoItem()
        self._sdr_item.setZValue(1)
        self._sdr_item.hide()

        self._scene = QGraphicsScene(self)
        self._scene.addItem(self._video_item)
        self._scene.addItem(self._sdr_item)

        self._video_view = QGraphicsView(self._scene, self)
        self._video_view.setFrameShape(QFrame.Shape.NoFrame)
        self._video_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Accept focus so keyboard navigation targets the video viewport without requiring the user
        # to click a non-interactive chrome element first.
        self._video_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self._video_view)
        # Match the photo viewer's light-toned surface so letterboxed video frames sit
        # on the same neutral backdrop.  The theme controller updates this later via
        # ``set_surface_color`` when the palette changes.
        surface_color = viewer_surface_color(self)
        self._default_surface_color = surface_color
        self._video_view.setStyleSheet("background: transparent; border: none;")
        self._video_view.viewport().setStyleSheet(
            f"background-color: {surface_color}; border: none;"
        )
        self.setStyleSheet(f"background-color: {surface_color};")
        self._scene.setBackgroundBrush(QColor(surface_color))
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

        # --- HDR Detection ---
        # Track whether the current media uses an HDR colour space so we can
        # switch to the SDR fallback rendering path.
        self._hdr_detected: bool = False
        self._hdr_checked: bool = False
        # Connect once to the video item's internal sink.  The sink object
        # persists across media changes, so one connection is sufficient.
        _sink = self._video_item.videoSink()
        if _sink is not None:
            _sink.videoFrameChanged.connect(self._on_video_frame_changed)
        # --- End HDR Detection ---

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

    def _apply_surface(self, colour: str) -> None:
        """Apply *colour* to the scene background, viewport, and widget."""

        stylesheet = f"background-color: {colour}; border: none;"
        self.setStyleSheet(stylesheet)
        self._video_view.setStyleSheet("background: transparent; border: none;")
        self._video_view.viewport().setStyleSheet(stylesheet)
        self._scene.setBackgroundBrush(QColor(colour))

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
        # Reset HDR detection so the first decoded frame re-evaluates
        # whether the new media requires the SDR fallback path.
        self._hdr_detected = False
        self._hdr_checked = False
        self._video_item.show()
        self._sdr_item.clearFrame()
        self._sdr_item.hide()

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
            # Step back a few milliseconds and pause so the last visible
            # frame remains on screen instead of flashing to black.
            hold_pos = max(0, duration - VIDEO_COMPLETE_HOLD_BACKSTEP_MS)
            self._player.setPosition(hold_pos)
            self._player.pause()
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
        scene_size = self._scene.sceneRect().size()
        self._video_item.setSize(scene_size)
        self._video_item.setPos(QPointF())
        self._sdr_item.setSize(QSizeF(scene_size))
        self._sdr_item.setPos(QPointF())
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

    # ------------------------------------------------------------------
    # HDR → SDR fallback rendering
    # ------------------------------------------------------------------
    def _on_video_frame_changed(self, frame: "QVideoFrame") -> None:
        """Handle each decoded video frame from the internal sink.

        On the first frame of a new media source we inspect the colour-space
        metadata.  If HDR is detected the standard ``QGraphicsVideoItem`` is
        hidden and subsequent frames are tone-mapped to SDR via
        ``QVideoFrame.toImage()`` and painted through :class:`_SdrVideoItem`.
        """

        if not self._hdr_checked:
            self._hdr_checked = True
            fmt = frame.surfaceFormat()
            if _is_hdr_frame_format(fmt):
                _log.debug("HDR content detected – activating SDR fallback renderer")
                self._hdr_detected = True
                self._video_item.hide()
                self._sdr_item.show()

        if self._hdr_detected:
            self._sdr_item.updateFrame(frame)

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
