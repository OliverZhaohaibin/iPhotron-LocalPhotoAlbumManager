"""Opt-in Windows OpenGL fullscreen composition workaround.

Qt documents WS_BORDER for native fullscreen OpenGL/DWM composition problems:
https://doc.qt.io/qt-6/windows-issues.html#fullscreen-opengl-based-windows
This is a candidate workaround, not proof of the cause of a particular flicker.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QSurface

from .detail_profile import emit_detail_event

_LOGGER = logging.getLogger(__name__)
_WS_BORDER = 0x00800000
_GWL_STYLE = -16


class _WindowStyles:
    """Bind pointer-sized Win32 APIs only when used on Windows."""

    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        pointer64 = ctypes.sizeof(ctypes.c_void_p) == 8
        self._get = getattr(user32, "GetWindowLongPtrW" if pointer64 else "GetWindowLongW")
        self._set = getattr(user32, "SetWindowLongPtrW" if pointer64 else "SetWindowLongW")
        long_type = ctypes.c_ssize_t if pointer64 else ctypes.c_int32
        self._get.argtypes = (ctypes.c_void_p, ctypes.c_int32)
        self._get.restype = long_type
        self._set.argtypes = (ctypes.c_void_p, ctypes.c_int32, long_type)
        self._set.restype = long_type

    def get(self, hwnd: int) -> int:
        ctypes.set_last_error(0)
        value = self._get(hwnd, _GWL_STYLE)
        error = ctypes.get_last_error()
        if not value and error:
            raise OSError(error, "GetWindowLongPtrW failed")
        return int(value)

    def set(self, hwnd: int, style: int) -> None:
        ctypes.set_last_error(0)
        previous = self._set(hwnd, _GWL_STYLE, style)
        error = ctypes.get_last_error()
        if not previous and error:
            raise OSError(error, "SetWindowLongPtrW failed")


class WindowsFullscreenCompositionGuard(QObject):
    """Change only the style of an already-created fullscreen OpenGL HWND.

    Qt saves its normal style before entering fullscreen, then restores it on
    exit. Adding the bit after entry leaves that saved normal style untouched.
    Never use setWindowFlags, recreate/reparent a surface, or move/resize the
    window here. Like Qt's documented workaround, no frame recalculation is
    forced; fullscreen geometry remains owned by Qt.
    """

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self._api = None
        self._applying = False
        self.verification_count = 0
        window.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._window and event.type() in {
            QEvent.Type.WindowStateChange,
            QEvent.Type.WinIdChange,
            QEvent.Type.Show,
        }:
            self.apply_if_fullscreen()
        return False

    def apply_if_fullscreen(self) -> None:
        if self._applying or not self._window.isFullScreen():
            return
        handle = self._window.windowHandle()
        if handle is None or handle.surfaceType() not in {
            QSurface.SurfaceType.OpenGLSurface,
            QSurface.SurfaceType.RasterGLSurface,
        }:
            return
        self._applying = True
        try:
            hwnd = int(self._window.internalWinId())
            if not hwnd:
                return
            if self._api is None:
                self._api = _WindowStyles()
            before = self._api.get(hwnd)
            if before & _WS_BORDER:
                self.verification_count += 1
                return
            self._api.set(hwnd, before | _WS_BORDER)
            after = self._api.get(hwnd)
            applied = bool(after & _WS_BORDER)
            if applied:
                self.verification_count += 1
            emit_detail_event(
                "fullscreen_composition_border",
                generation=0,
                applied=applied,
                style_before=before,
                style_after=after,
            )
            if not applied:
                _LOGGER.warning("Windows fullscreen composition border was not retained")
        except (OSError, AttributeError, RuntimeError, ValueError):
            _LOGGER.exception("Windows fullscreen composition border unavailable")
            emit_detail_event("fullscreen_composition_border", generation=0, applied=False)
        finally:
            self._applying = False


def install_fullscreen_composition_guard(window):
    """Keep the unverified compatibility path explicitly opt-in."""
    if sys.platform != "win32" or os.environ.get("IPHOTO_WINDOWS_FULLSCREEN_BORDER", "") != "1":
        return None
    return WindowsFullscreenCompositionGuard(window)
