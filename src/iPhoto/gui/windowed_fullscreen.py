"""Windows OpenGL windowed-fullscreen compatibility for exact-screen flicker.

Keep the resident HWND/QRhi hierarchy, but avoid native fullscreen and an exact
monitor-sized surface. The extra logical pixel is outside the selected screen.
Windows OpenGL uses this by default; an explicit zero restores native fullscreen
for comparisons. Other platforms and graphics APIs retain their native behavior.
"""

from __future__ import annotations

import logging
import os
import sys
from enum import Enum

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QSurface

from .detail_profile import emit_detail_event

_ACTIVE = "_iphoto_windowed_fullscreen_active"
_OVERRIDE = "IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN"
_LOGGER = logging.getLogger(__name__)


def is_media_fullscreen(window) -> bool:
    return window is not None and (window.property(_ACTIVE) is True or bool(window.isFullScreen()))


class FullscreenPhase(str, Enum):
    INACTIVE = "inactive"
    ENTERING_WINDOWED = "entering_windowed"
    WINDOWED = "windowed"
    ENTERING_NATIVE = "entering_native"
    NATIVE = "native"


def is_media_fullscreen_state_event(event) -> bool:
    """Window owners reconcile after both native and logical state changes."""
    return event.type() == QEvent.Type.WindowStateChange or (
        event.type() == QEvent.Type.DynamicPropertyChange
        and bytes(event.propertyName()) == _ACTIVE.encode("ascii")
    )


class WindowedFullscreenController(QObject):
    """Own one bounded fullscreen transition on the existing native window."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self._geometry = None
        self._state = Qt.WindowState.WindowNoState
        self._applying = False
        self._attempt_key = None
        self._attempts = 0
        self._epoch = 0
        self._pending_retry = None
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._dispatch_retry)
        self.phase = FullscreenPhase.INACTIVE
        self.verification_count = 0
        window.installEventFilter(self)
        handle = window.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(lambda _screen: self.reflow())

    def _cancel_retry(self) -> None:
        self._retry_timer.stop()
        self._pending_retry = None

    def _queue_retry(self, kind: str) -> None:
        if self._pending_retry is not None:
            return
        epoch, key = self._epoch, self._attempt_key

        def retry():
            if epoch != self._epoch or key != self._attempt_key:
                return
            if kind == "windowed":
                self.reflow()
            elif kind == "close":
                # closeEvent can be ignored (e.g. cancel an unsaved-edit dialog).
                # Decide only after Qt has delivered it, and never reopen an
                # accepted close via an already queued geometry/native retry.
                if not self.window.isVisible():
                    self._deactivate("window_closed")
                elif self.phase == FullscreenPhase.ENTERING_NATIVE:
                    self._queue_retry("native")
                else:
                    self.reflow()
            elif self.phase == FullscreenPhase.ENTERING_NATIVE:
                self._verify_native_fallback()

        self._pending_retry = retry
        self._retry_timer.start(0)

    def _dispatch_retry(self) -> None:
        callback = self._pending_retry
        self._pending_retry = None
        if callback is not None:
            callback()

    def enter(self) -> None:
        if self.phase != FullscreenPhase.INACTIVE:
            return
        self._geometry = self.window.saveGeometry()
        self._state = self.window.windowState()
        self._epoch += 1
        self._cancel_retry()
        self._attempt_key, self._attempts = None, 0
        self.phase = FullscreenPhase.ENTERING_WINDOWED
        # Pending entry counts as immersive until a bounded terminal outcome.
        self.window.setProperty(_ACTIVE, True)
        self._applying = True
        try:
            self.window.showNormal()
        finally:
            self._applying = False
        self.reflow()
        self.window.raise_()

    def _deactivate(self, reason: str) -> None:
        self._epoch += 1
        self._cancel_retry()
        self.phase = FullscreenPhase.INACTIVE
        self.window.setProperty(_ACTIVE, False)
        emit_detail_event("fullscreen_session_ended", generation=0, reason=reason)

    def _restore_window(self) -> None:
        self.window.showNormal()
        if self._geometry is not None:
            self.window.restoreGeometry(self._geometry)
        self.window.setWindowState(self._state & ~Qt.WindowState.WindowFullScreen)

    def exit(self) -> None:
        if self.phase == FullscreenPhase.INACTIVE:
            return
        self._deactivate("requested_exit")
        self._restore_window()

    def reflow(self) -> None:
        window = self.window
        if (
            self._applying
            or self.phase
            not in {
                FullscreenPhase.ENTERING_WINDOWED,
                FullscreenPhase.WINDOWED,
            }
            or window.isMinimized()
            or not window.isVisible()
        ):
            return
        if window.isMaximized() and not window.isFullScreen():
            self._deactivate("system_maximized")
            return
        screen = window.screen()
        rect = QRect(screen.geometry()) if screen is not None else QRect()
        target = rect.adjusted(0, 0, 0, 1) if not rect.isEmpty() else QRect()
        key = tuple(target.getRect())
        if key != self._attempt_key:
            self._cancel_retry()
            self._attempt_key, self._attempts = key, 0
        if not target.isEmpty() and window.geometry() == target and not window.isFullScreen():
            self.phase = FullscreenPhase.WINDOWED
            self.verification_count += 1
            self._cancel_retry()
            return
        if self._attempts >= 3:
            self._start_native_fallback("geometry_rejected")
            return
        self._attempts += 1
        self._applying = True
        error = None
        try:
            if not target.isEmpty():
                window.setGeometry(target)
        except (RuntimeError, ValueError) as exception:
            error = type(exception).__name__
        finally:
            self._applying = False
        applied = (
            error is None
            and not target.isEmpty()
            and window.geometry() == target
            and not window.isFullScreen()
        )
        emit_detail_event(
            "fullscreen_composition_overscan",
            generation=0,
            applied=applied,
            screen=list(rect.getRect()),
            target=list(target.getRect()),
            actual=list(window.geometry().getRect()),
            qt_fullscreen=window.isFullScreen(),
            native_id=int(window.internalWinId()),
            attempt=self._attempts,
            error=error,
        )
        if applied:
            self.phase = FullscreenPhase.WINDOWED
            self.verification_count += 1
            self._cancel_retry()
        elif self._attempts >= 3:
            self._start_native_fallback(error or "geometry_rejected")
        else:
            self._queue_retry("windowed")

    def _start_native_fallback(self, reason: str) -> None:
        if self.phase not in {FullscreenPhase.ENTERING_WINDOWED, FullscreenPhase.WINDOWED}:
            return
        self._cancel_retry()
        self.phase = FullscreenPhase.ENTERING_NATIVE
        _LOGGER.warning(
            "Overscan failed after %s attempts; falling back to native fullscreen: %s",
            self._attempts,
            reason,
        )
        emit_detail_event(
            "fullscreen_native_fallback", generation=0, reason=reason, attempts=self._attempts
        )
        self._applying = True
        try:
            self.window.showFullScreen()
        except RuntimeError:
            _LOGGER.exception("Native fullscreen request failed")
        finally:
            self._applying = False
        self._queue_retry("native")

    def _verify_native_fallback(self) -> None:
        if self.window.isMinimized():
            return  # WindowStateChange verifies again on restore, without a busy loop.
        if self.window.isFullScreen():
            self.phase = FullscreenPhase.NATIVE
            emit_detail_event("fullscreen_native_fallback_verified", generation=0)
            return
        _LOGGER.warning("Native fullscreen fallback did not enter; restoring the original window")
        self._deactivate("native_fallback_failed")
        self._restore_window()

    def eventFilter(self, watched, event) -> bool:
        if watched is not self.window or self._applying or self.phase == FullscreenPhase.INACTIVE:
            return False
        if event.type() == QEvent.Type.Close:
            self._epoch += 1
            self._cancel_retry()
            self._queue_retry("close")
        elif event.type() == QEvent.Type.WindowStateChange:
            if self.window.isMinimized():
                return False
            if self.window.isMaximized() and not self.window.isFullScreen():
                self._deactivate("system_maximized")
            elif self.phase == FullscreenPhase.ENTERING_NATIVE:
                self._queue_retry("native")
            elif self.phase == FullscreenPhase.NATIVE:
                if not self.window.isFullScreen():
                    self._deactivate("system_exit")
            else:
                self.reflow()
        elif event.type() in {
            QEvent.Type.Show,
            QEvent.Type.Resize,
            QEvent.Type.DevicePixelRatioChange,
        }:
            if self.phase == FullscreenPhase.ENTERING_NATIVE and event.type() == QEvent.Type.Show:
                self._queue_retry("native")
            else:
                self.reflow()
        return False


def _use_windowed_fullscreen(window) -> bool:
    if sys.platform != "win32":
        return False
    override = os.environ.get(_OVERRIDE, "auto").strip().lower()
    if override in {"0", "false", "no", "off"}:
        return False
    if override not in {"", "auto", "1", "true", "yes", "on"}:
        _LOGGER.warning(
            "Ignoring unsupported %s=%r; using Windows OpenGL default", _OVERRIDE, override
        )
    handle = window.windowHandle()
    return handle is not None and handle.surfaceType() in {
        QSurface.SurfaceType.OpenGLSurface,
        QSurface.SurfaceType.RasterGLSurface,
    }


def enter_media_fullscreen(window) -> None:
    use_windowed = _use_windowed_fullscreen(window)
    strategy = "windowed_overscan" if use_windowed else "native"
    handle = window.windowHandle()
    surface = handle.surfaceType().name if handle is not None else "unavailable"
    override = os.environ.get(_OVERRIDE, "auto")
    # Ordinary IDE/packaged launches need this evidence too; profiling need not
    # be enabled to tell which policy was actually selected.
    _LOGGER.info(
        "Media fullscreen strategy=%s platform=%s surface=%s override=%r",
        strategy,
        sys.platform,
        surface,
        override,
    )
    emit_detail_event(
        "fullscreen_strategy_selected",
        generation=0,
        strategy=strategy,
        platform=sys.platform,
        surface=surface,
        override=override,
    )
    if not use_windowed:
        window.showFullScreen()
        return
    controller = getattr(window, "_iphoto_fullscreen_controller", None)
    if not isinstance(controller, WindowedFullscreenController):
        controller = WindowedFullscreenController(window)
        window._iphoto_fullscreen_controller = controller
    controller.enter()


def exit_media_fullscreen(window) -> None:
    controller = getattr(window, "_iphoto_fullscreen_controller", None)
    if isinstance(controller, WindowedFullscreenController) and window.property(_ACTIVE) is True:
        controller.exit()
    else:
        window.showNormal()
