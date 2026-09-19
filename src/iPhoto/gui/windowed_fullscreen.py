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

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QSurface

from .detail_profile import emit_detail_event

_ACTIVE = "_iphoto_windowed_fullscreen_active"
_OVERRIDE = "IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN"
_LOGGER = logging.getLogger(__name__)


def is_media_fullscreen(window) -> bool:
    return window is not None and (window.property(_ACTIVE) is True or bool(window.isFullScreen()))


class WindowedFullscreenController(QObject):
    """Own presentation geometry without replacing Qt's native window."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self._geometry = None
        self._state = Qt.WindowState.WindowNoState
        self._applying = False
        self._attempt_key = None
        self._attempts = 0
        self.verification_count = 0
        window.installEventFilter(self)
        handle = window.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(lambda _screen: self.reflow())

    def enter(self) -> None:
        if self.window.property(_ACTIVE) is True:
            return
        self._geometry = self.window.saveGeometry()
        self._state = self.window.windowState()
        self._attempt_key = None
        self._attempts = 0
        # Set the logical state before showNormal emits native state events.
        self.window.setProperty(_ACTIVE, True)
        self.window.showNormal()
        self.reflow()
        self.window.raise_()

    def exit(self) -> None:
        if self.window.property(_ACTIVE) is not True:
            return
        self.window.setProperty(_ACTIVE, False)
        self.window.showNormal()
        if self._geometry is not None:
            self.window.restoreGeometry(self._geometry)
        self.window.setWindowState(self._state & ~Qt.WindowState.WindowFullScreen)

    def reflow(self) -> None:
        window = self.window
        if self._applying or window.property(_ACTIVE) is not True or window.isMinimized():
            return
        screen = window.screen()
        if screen is None or screen.geometry().isEmpty():
            return
        rect = QRect(screen.geometry())
        target = rect.adjusted(0, 0, 0, 1)
        if window.geometry() == target and not window.isFullScreen():
            self.verification_count += 1
            return
        key = tuple(target.getRect())
        if key != self._attempt_key:
            self._attempt_key, self._attempts = key, 0
        if self._attempts >= 3:
            return
        self._attempts += 1
        self._applying = True
        try:
            window.setGeometry(target)
            applied = window.geometry() == target and not window.isFullScreen()
            if applied:
                self.verification_count += 1
            emit_detail_event(
                "fullscreen_composition_overscan",
                generation=0,
                applied=applied,
                screen=list(rect.getRect()),
                target=list(target.getRect()),
                actual=list(window.geometry().getRect()),
                qt_fullscreen=window.isFullScreen(),
                native_id=int(window.internalWinId()),
            )
        finally:
            self._applying = False

    def eventFilter(self, watched, event) -> bool:
        if watched is self.window and self.window.property(_ACTIVE) is True:
            if event.type() == QEvent.Type.WindowStateChange and self.window.isMaximized():
                # An OS snap/maximize action leaves immersive presentation.
                self.window.setProperty(_ACTIVE, False)
            elif event.type() in {
                QEvent.Type.Show,
                QEvent.Type.Resize,
                QEvent.Type.WindowStateChange,
                QEvent.Type.DevicePixelRatioChange,
            }:
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
