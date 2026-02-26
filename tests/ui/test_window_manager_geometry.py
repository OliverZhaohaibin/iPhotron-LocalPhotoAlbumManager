"""Regression tests for frameless window geometry clamping across screen changes."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QPoint, QRect, QSize

from iPhoto.gui.ui.window_manager import FramelessWindowManager


class _FakeScreen:
    def __init__(self, rect: QRect, dpr: float) -> None:
        self._rect = rect
        self._dpr = dpr

    def availableGeometry(self) -> QRect:  # noqa: N802 - Qt naming
        return QRect(self._rect)

    def devicePixelRatio(self) -> float:  # noqa: N802 - Qt naming
        return self._dpr


def test_clamp_size_to_available_limits_by_screen() -> None:
    """Window size should never exceed the target screen's available area."""

    current = QSize(6000, 4000)
    clamped = FramelessWindowManager._clamp_size_to_available(current, 1920, 1080)

    assert clamped.width() == 1880
    assert clamped.height() == 1040


def test_apply_screen_change_fix_rescales_and_repositions() -> None:
    """Moving to a denser screen should shrink and pull the window back on-screen."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._geometry_fix_in_progress = False
    manager._last_screen_dpr = 1.0

    frame_rect = QRect(5000, 5000, 2200, 1600)

    window = MagicMock()
    window.size.return_value = QSize(2200, 1600)
    window.frameGeometry.return_value = frame_rect
    manager._window = window

    target_screen = _FakeScreen(QRect(0, 0, 2560, 1440), dpr=2.0)
    manager._apply_screen_change_fix(1.0, target_screen)

    window.resize.assert_called_once_with(QSize(1100, 800))
    window.move.assert_called_once_with(QPoint(20, 20))
    assert manager._last_screen_dpr == 2.0
