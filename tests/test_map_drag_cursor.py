from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for map cursor tests", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication, QWidget

from maps.map_widget.drag_cursor import DragCursorManager


@pytest.fixture
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _clear_override_cursors() -> None:
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


def test_drag_cursor_manager_sets_widget_and_override_cursor(qapp: QApplication) -> None:
    del qapp
    _clear_override_cursors()
    target = QWidget()
    manager = DragCursorManager()

    try:
        manager.set_cursor(Qt.CursorShape.ClosedHandCursor, (target,))

        override_cursor = QApplication.overrideCursor()
        assert target.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert override_cursor is not None
        assert override_cursor.shape() == Qt.CursorShape.ClosedHandCursor

        manager.reset((target,))

        assert target.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert QApplication.overrideCursor() is None
    finally:
        manager.reset((target,))
        target.close()
        _clear_override_cursors()


def test_drag_cursor_manager_sets_qwindow_and_override_cursor(qapp: QApplication) -> None:
    del qapp
    _clear_override_cursors()
    target = QWindow()
    manager = DragCursorManager()

    try:
        manager.set_cursor(Qt.CursorShape.ClosedHandCursor, (target,))

        override_cursor = QApplication.overrideCursor()
        assert target.cursor().shape() == Qt.CursorShape.ClosedHandCursor
        assert override_cursor is not None
        assert override_cursor.shape() == Qt.CursorShape.ClosedHandCursor

        manager.reset((target,))

        assert target.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert QApplication.overrideCursor() is None
    finally:
        manager.reset((target,))
        target.destroy()
        _clear_override_cursors()


def test_drag_cursor_manager_uses_native_cursor_stack_once(qapp: QApplication) -> None:
    del qapp
    _clear_override_cursors()
    target = QWidget()
    manager = DragCursorManager()

    class _FakeCursorStack:
        def __init__(self) -> None:
            self.pushes = 0
            self.refreshes = 0
            self.pops = 0

        def push_closed_hand(self) -> bool:
            self.pushes += 1
            return True

        def refresh_closed_hand(self) -> None:
            self.refreshes += 1

        def pop(self) -> None:
            self.pops += 1

    fake_stack = _FakeCursorStack()
    manager._mac_cursor_stack = fake_stack

    try:
        manager.set_cursor(Qt.CursorShape.ClosedHandCursor, (target,))
        manager.set_cursor(Qt.CursorShape.ClosedHandCursor, (target,))

        assert fake_stack.pushes == 1
        assert fake_stack.refreshes == 1
        assert fake_stack.pops == 0

        manager.reset((target,))

        assert fake_stack.pops == 1
        assert QApplication.overrideCursor() is None
    finally:
        manager.reset((target,))
        target.close()
        _clear_override_cursors()
