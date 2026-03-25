"""Tests for frameless window geometry clamping, screen changes, and Wayland drag."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtCore import QEvent, QPoint, QRect, QSize
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import Qt

from iPhoto.gui.ui.window_manager import FramelessWindowManager, _is_wayland


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
    window.isFullScreen.return_value = False
    window.isMaximized.return_value = False
    manager._window = window

    snap_helper = MagicMock()
    snap_helper.is_snapped.return_value = False
    manager._snap_helper = snap_helper

    target_screen = _FakeScreen(QRect(0, 0, 2560, 1440), dpr=2.0)
    manager._apply_screen_change_fix(1.0, target_screen)

    window.resize.assert_called_once_with(QSize(1100, 800))

    # ``QWidget.move`` is overloaded in Qt and may be invoked either as
    # ``move(QPoint)`` or ``move(x, y)`` depending on binding/runtime details.
    # Accept both call signatures to keep the regression test platform-stable.
    window.move.assert_called_once()
    args, _ = window.move.call_args
    assert args in ((QPoint(20, 20),), (20, 20))
    assert manager._last_screen_dpr == 2.0


# ---------------------------------------------------------------------------
# _is_wayland helper
# ---------------------------------------------------------------------------

class TestIsWayland:
    """Unit tests for the _is_wayland() platform-detection helper."""

    def test_returns_false_on_non_linux(self) -> None:
        """Non-Linux platforms must never be identified as Wayland."""
        with patch("iPhoto.gui.ui.window_manager.sys") as mock_sys:
            mock_sys.platform = "darwin"
            assert _is_wayland() is False

    def test_returns_false_on_windows(self) -> None:
        with patch("iPhoto.gui.ui.window_manager.sys") as mock_sys:
            mock_sys.platform = "win32"
            assert _is_wayland() is False

    def test_returns_false_when_no_app(self) -> None:
        """If no QApplication is running the result is False (not an error)."""
        with patch("iPhoto.gui.ui.window_manager.sys") as mock_sys, \
             patch("iPhoto.gui.ui.window_manager.QApplication") as mock_app_cls:
            mock_sys.platform = "linux"
            mock_app_cls.instance.return_value = None
            assert _is_wayland() is False

    def test_returns_true_on_linux_wayland(self) -> None:
        """Linux + platformName() == 'wayland' must return True."""
        mock_app = MagicMock()
        mock_app.platformName.return_value = "wayland"
        with patch("iPhoto.gui.ui.window_manager.sys") as mock_sys, \
             patch("iPhoto.gui.ui.window_manager.QApplication") as mock_app_cls:
            mock_sys.platform = "linux"
            mock_app_cls.instance.return_value = mock_app
            assert _is_wayland() is True

    def test_returns_false_on_linux_xcb(self) -> None:
        """Linux + platformName() == 'xcb' (X11) must return False."""
        mock_app = MagicMock()
        mock_app.platformName.return_value = "xcb"
        with patch("iPhoto.gui.ui.window_manager.sys") as mock_sys, \
             patch("iPhoto.gui.ui.window_manager.QApplication") as mock_app_cls:
            mock_sys.platform = "linux"
            mock_app_cls.instance.return_value = mock_app
            assert _is_wayland() is False


# ---------------------------------------------------------------------------
# Wayland drag: startSystemMove() integration
# ---------------------------------------------------------------------------

def _make_press_event(global_pos: QPoint) -> QMouseEvent:
    """Create a minimal left-button MouseButtonPress event at *global_pos*."""
    event = MagicMock(spec=QMouseEvent)
    event.type.return_value = QEvent.Type.MouseButtonPress
    event.button.return_value = Qt.MouseButton.LeftButton
    event.globalPosition.return_value = MagicMock()
    event.globalPosition.return_value.toPoint.return_value = global_pos
    return event


def _make_manager_for_drag() -> FramelessWindowManager:
    """Return a FramelessWindowManager stub wired for drag tests."""
    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = False
    manager._drag_active = False
    manager._drag_offset = QPoint()
    manager._snap_helper = MagicMock()
    manager._snap_helper.is_snapped.return_value = False

    win_handle = MagicMock()
    win_handle.startSystemMove.return_value = True

    window = MagicMock()
    window.frameGeometry.return_value = QRect(100, 100, 800, 600)
    window.width.return_value = 800
    window.windowHandle.return_value = win_handle
    manager._window = window
    return manager


class TestWaylandDrag:
    """Verify that startSystemMove() is used on Linux+Wayland and not elsewhere."""

    def test_wayland_drag_calls_start_system_move(self) -> None:
        """On Linux+Wayland, a title-bar press should trigger startSystemMove()."""
        manager = _make_manager_for_drag()
        event = _make_press_event(QPoint(200, 110))

        with patch("iPhoto.gui.ui.window_manager._is_wayland", return_value=True):
            result = manager._handle_title_bar_drag(event)

        assert result is True
        manager._window.windowHandle().startSystemMove.assert_called_once()
        # Manual drag state must NOT be entered on Wayland.
        assert manager._drag_active is False

    def test_wayland_fallback_when_start_system_move_unsupported(self) -> None:
        """If startSystemMove() returns False, fall back to the manual drag path."""
        manager = _make_manager_for_drag()
        manager._window.windowHandle().startSystemMove.return_value = False
        event = _make_press_event(QPoint(200, 110))

        with patch("iPhoto.gui.ui.window_manager._is_wayland", return_value=True):
            result = manager._handle_title_bar_drag(event)

        assert result is True
        # Manual drag state should be entered as a fallback.
        assert manager._drag_active is True

    def test_non_wayland_does_not_call_start_system_move(self) -> None:
        """On X11/Windows/macOS the manual drag path must be used exclusively."""
        manager = _make_manager_for_drag()
        event = _make_press_event(QPoint(200, 110))

        with patch("iPhoto.gui.ui.window_manager._is_wayland", return_value=False):
            result = manager._handle_title_bar_drag(event)

        assert result is True
        manager._window.windowHandle().startSystemMove.assert_not_called()
        assert manager._drag_active is True

    def test_wayland_drag_ignored_in_immersive_mode(self) -> None:
        """Dragging must be a no-op when immersive/fullscreen mode is active."""
        manager = _make_manager_for_drag()
        manager._immersive_active = True
        event = _make_press_event(QPoint(200, 110))

        with patch("iPhoto.gui.ui.window_manager._is_wayland", return_value=True):
            result = manager._handle_title_bar_drag(event)

        assert result is False
        manager._window.windowHandle().startSystemMove.assert_not_called()
