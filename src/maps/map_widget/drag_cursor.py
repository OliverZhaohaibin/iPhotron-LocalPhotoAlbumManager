"""Helpers for keeping map drag cursors visible across embedded Qt surfaces."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys
from collections.abc import Iterable
from typing import Protocol

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

LOGGER = logging.getLogger(__name__)


class CursorTarget(Protocol):
    def setCursor(self, cursor) -> None:  # pragma: no cover - Qt implementation
        ...

    def unsetCursor(self) -> None:  # pragma: no cover - Qt implementation
        ...


class _MacOSCursorStack:
    """Tiny ctypes bridge for AppKit cursor push/pop without a PyObjC dependency."""

    def __init__(self) -> None:
        self._available = sys.platform == "darwin"
        self._objc: ctypes.CDLL | None = None
        self._appkit: ctypes.CDLL | None = None
        self._send_id = None
        self._send_void = None
        self._ns_cursor: int | None = None
        self._closed_hand_cursor: int | None = None
        self._push_selector: int | None = None
        self._pop_selector: int | None = None
        self._set_selector: int | None = None

    def push_closed_hand(self) -> bool:
        if not self._ensure_loaded():
            return False
        assert self._closed_hand_cursor is not None
        assert self._push_selector is not None
        assert self._set_selector is not None
        assert self._send_void is not None
        self._send_void(self._closed_hand_cursor, self._push_selector)
        self._send_void(self._closed_hand_cursor, self._set_selector)
        return True

    def refresh_closed_hand(self) -> None:
        if not self._ensure_loaded():
            return
        assert self._closed_hand_cursor is not None
        assert self._set_selector is not None
        assert self._send_void is not None
        self._send_void(self._closed_hand_cursor, self._set_selector)

    def pop(self) -> None:
        if not self._ensure_loaded():
            return
        assert self._ns_cursor is not None
        assert self._pop_selector is not None
        assert self._send_void is not None
        self._send_void(self._ns_cursor, self._pop_selector)

    def _ensure_loaded(self) -> bool:
        if not self._available:
            return False
        if self._closed_hand_cursor is not None:
            return True

        try:
            objc_path = ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib"
            appkit_path = (
                ctypes.util.find_library("AppKit")
                or "/System/Library/Frameworks/AppKit.framework/AppKit"
            )
            objc = ctypes.CDLL(objc_path)
            appkit = ctypes.CDLL(appkit_path)
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
                ("objc_msgSend", objc)
            )
            send_void = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(
                ("objc_msgSend", objc)
            )

            ns_cursor = objc.objc_getClass(b"NSCursor")
            closed_hand_selector = objc.sel_registerName(b"closedHandCursor")
            push_selector = objc.sel_registerName(b"push")
            pop_selector = objc.sel_registerName(b"pop")
            set_selector = objc.sel_registerName(b"set")
            closed_hand_cursor = send_id(ns_cursor, closed_hand_selector)
            if not ns_cursor or not closed_hand_cursor:
                self._available = False
                return False
        except Exception:
            self._available = False
            LOGGER.debug("AppKit cursor bridge is unavailable", exc_info=True)
            return False

        self._objc = objc
        self._appkit = appkit
        self._send_id = send_id
        self._send_void = send_void
        self._ns_cursor = int(ns_cursor)
        self._closed_hand_cursor = int(closed_hand_cursor)
        self._push_selector = int(push_selector)
        self._pop_selector = int(pop_selector)
        self._set_selector = int(set_selector)
        return True


class DragCursorManager:
    """Apply drag cursors to map widgets and the process-wide Qt override stack."""

    def __init__(self) -> None:
        self._override_active = False
        self._mac_cursor_stack = _MacOSCursorStack()
        self._mac_cursor_active = False

    def set_cursor(self, cursor_shape: Qt.CursorShape, targets: Iterable[CursorTarget]) -> None:
        cursor = QCursor(cursor_shape)
        for target in targets:
            try:
                target.setCursor(cursor)
            except (AttributeError, RuntimeError, TypeError):
                continue

        if QApplication.instance() is None:
            return

        if self._override_active:
            QApplication.changeOverrideCursor(cursor)
        else:
            QApplication.setOverrideCursor(cursor)
            self._override_active = True

        if cursor_shape == Qt.CursorShape.ClosedHandCursor:
            if self._mac_cursor_active:
                self._mac_cursor_stack.refresh_closed_hand()
            else:
                self._mac_cursor_active = self._mac_cursor_stack.push_closed_hand()

    def reset(self, targets: Iterable[CursorTarget]) -> None:
        for target in targets:
            try:
                target.unsetCursor()
            except (AttributeError, RuntimeError, TypeError):
                continue

        if self._override_active and QApplication.instance() is not None:
            QApplication.restoreOverrideCursor()
        self._override_active = False
        if self._mac_cursor_active:
            self._mac_cursor_stack.pop()
            self._mac_cursor_active = False


__all__ = ["CursorTarget", "DragCursorManager"]
