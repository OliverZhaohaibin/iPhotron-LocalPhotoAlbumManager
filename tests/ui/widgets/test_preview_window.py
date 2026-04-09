from __future__ import annotations

from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for preview window tests", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.preview_window import PreviewWindow, _PreviewWheelGuard


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_do_close_unloads_media_and_hides_window() -> None:
    window = PreviewWindow.__new__(PreviewWindow)
    window._close_timer = Mock()
    window._media = Mock()
    window._rhi_popup = Mock()
    window.hide = Mock()

    PreviewWindow._do_close(window)

    window._close_timer.stop.assert_called_once_with()
    window._media.unload.assert_called_once_with()
    window._rhi_popup.close_preview.assert_called_once_with()
    window.hide.assert_called_once_with()


def test_preview_wheel_guard_blocks_wheel_events(qapp) -> None:
    del qapp
    guard = _PreviewWheelGuard()
    event = QWheelEvent(
        QPointF(12.0, 18.0),
        QPointF(12.0, 18.0),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    event.ignore()

    assert guard.eventFilter(Mock(), event) is True
    assert event.isAccepted()


def test_preview_wheel_guard_ignores_non_wheel_events(qapp) -> None:
    del qapp
    guard = _PreviewWheelGuard()
    event = QEvent(QEvent.Type.MouseMove)

    assert guard.eventFilter(Mock(), event) is False
