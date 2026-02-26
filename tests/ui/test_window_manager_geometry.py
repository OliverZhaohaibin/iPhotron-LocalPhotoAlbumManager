"""Regression tests for frameless window geometry clamping across screen changes."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt

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

    # ``QWidget.move`` is overloaded in Qt and may be invoked either as
    # ``move(QPoint)`` or ``move(x, y)`` depending on binding/runtime details.
    # Accept both call signatures to keep the regression test platform-stable.
    window.move.assert_called_once()
    args, _ = window.move.call_args
    assert args in ((QPoint(20, 20),), (20, 20))
    assert manager._last_screen_dpr == 2.0


def test_start_system_move_uses_qwindow_api_on_windows(monkeypatch) -> None:
    """Windows frameless windows should delegate dragging to the OS move loop."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    handle = MagicMock()
    handle.startSystemMove.return_value = True

    window = MagicMock()
    window.windowHandle.return_value = handle
    manager._window = window

    monkeypatch.setattr("iPhoto.gui.ui.window_manager.sys.platform", "win32")

    assert manager._start_system_move() is True
    handle.startSystemMove.assert_called_once_with()


def test_start_system_resize_uses_qwindow_api_on_windows(monkeypatch) -> None:
    """Windows frameless resize grips should delegate to the system resize loop."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    handle = MagicMock()
    handle.startSystemResize.return_value = True

    window = MagicMock()
    window.windowHandle.return_value = handle
    manager._window = window

    monkeypatch.setattr("iPhoto.gui.ui.window_manager.sys.platform", "win32")

    edges = Qt.Edge.BottomEdge | Qt.Edge.RightEdge
    assert manager._start_system_resize(edges) is True
    handle.startSystemResize.assert_called_once_with(edges)


def test_title_drag_retries_system_move_on_mouse_move(monkeypatch) -> None:
    """Windows drag should retry system move on mouse-move before fallback."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = False
    manager._drag_active = False
    manager._system_move_armed = False

    window = MagicMock()
    window.frameGeometry.return_value.topLeft.return_value = QPoint(0, 0)
    manager._window = window

    monkeypatch.setattr("iPhoto.gui.ui.window_manager.sys.platform", "win32")

    move_attempts = {"count": 0}

    def _fake_start_system_move() -> bool:
        move_attempts["count"] += 1
        return move_attempts["count"] == 2

    manager._start_system_move = _fake_start_system_move  # type: ignore[method-assign]

    press = MagicMock()
    press.type.return_value = QEvent.Type.MouseButtonPress
    press.button.return_value = Qt.MouseButton.LeftButton
    press.globalPosition.return_value.toPoint.return_value = QPoint(10, 10)

    move = MagicMock()
    move.type.return_value = QEvent.Type.MouseMove
    move.buttons.return_value = Qt.MouseButton.LeftButton
    move.globalPosition.return_value.toPoint.return_value = QPoint(20, 20)

    assert manager._handle_title_bar_drag(press) is True
    assert manager._drag_active is True
    assert manager._system_move_armed is True

    assert manager._handle_title_bar_drag(move) is True
    assert manager._drag_active is False
    assert manager._system_move_armed is False
    window.move.assert_not_called()


def test_title_drag_falls_back_to_manual_move_when_system_move_fails(monkeypatch) -> None:
    """If system move stays unavailable, frameless dragging keeps working manually."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = False
    manager._drag_active = False
    manager._system_move_armed = False

    window = MagicMock()
    window.frameGeometry.return_value.topLeft.return_value = QPoint(5, 5)
    manager._window = window

    monkeypatch.setattr("iPhoto.gui.ui.window_manager.sys.platform", "win32")
    manager._start_system_move = lambda: False  # type: ignore[method-assign]

    press = MagicMock()
    press.type.return_value = QEvent.Type.MouseButtonPress
    press.button.return_value = Qt.MouseButton.LeftButton
    press.globalPosition.return_value.toPoint.return_value = QPoint(15, 15)

    move = MagicMock()
    move.type.return_value = QEvent.Type.MouseMove
    move.buttons.return_value = Qt.MouseButton.LeftButton
    move.globalPosition.return_value.toPoint.return_value = QPoint(25, 30)

    assert manager._handle_title_bar_drag(press) is True
    assert manager._handle_title_bar_drag(move) is True

    window.move.assert_called_once_with(QPoint(15, 20))


def test_title_drag_logs_warning_when_windows_fallback_happens(monkeypatch) -> None:
    """Fallback to manual move should emit a warning to aid troubleshooting."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = False
    manager._drag_active = False
    manager._system_move_armed = False

    window = MagicMock()
    window.frameGeometry.return_value.topLeft.return_value = QPoint(0, 0)
    manager._window = window

    monkeypatch.setattr("iPhoto.gui.ui.window_manager.sys.platform", "win32")
    manager._start_system_move = lambda: False  # type: ignore[method-assign]

    warning_mock = MagicMock()
    monkeypatch.setattr("iPhoto.gui.ui.window_manager._LOGGER.warning", warning_mock)

    press = MagicMock()
    press.type.return_value = QEvent.Type.MouseButtonPress
    press.button.return_value = Qt.MouseButton.LeftButton
    press.globalPosition.return_value.toPoint.return_value = QPoint(10, 10)

    move = MagicMock()
    move.type.return_value = QEvent.Type.MouseMove
    move.buttons.return_value = Qt.MouseButton.LeftButton
    move.globalPosition.return_value.toPoint.return_value = QPoint(15, 15)

    assert manager._handle_title_bar_drag(press) is True
    assert manager._handle_title_bar_drag(move) is True
    assert warning_mock.call_count >= 1


def test_start_system_resize_logs_edge_repr_without_typeerror(monkeypatch) -> None:
    """Diagnostic logging should handle Qt.Edge values without int() casting errors."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    handle = MagicMock()
    handle.startSystemResize.return_value = True

    window = MagicMock()
    window.windowHandle.return_value = handle
    manager._window = window

    monkeypatch.setattr("iPhoto.gui.ui.window_manager.sys.platform", "win32")

    diag_mock = MagicMock()
    monkeypatch.setattr(
        "iPhoto.gui.ui.window_manager.FramelessWindowManager._log_windows_snap_diagnostics",
        diag_mock,
    )

    edges = Qt.Edge.BottomEdge | Qt.Edge.RightEdge
    assert manager._start_system_resize(edges) is True
    assert any(
        call.args and call.args[0] == "start_system_resize_result"
        for call in diag_mock.call_args_list
    )


def test_title_drag_emits_info_trace_logs_on_windows(monkeypatch) -> None:
    """Windows title drag should emit info-level trace logs for troubleshooting."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._immersive_active = False
    manager._drag_active = False
    manager._system_move_armed = False
    manager._drag_trace_id = 0

    window = MagicMock()
    window.frameGeometry.return_value.topLeft.return_value = QPoint(0, 0)
    manager._window = window

    monkeypatch.setattr("iPhoto.gui.ui.window_manager.sys.platform", "win32")
    manager._start_system_move = lambda: False  # type: ignore[method-assign]

    info_mock = MagicMock()
    monkeypatch.setattr("iPhoto.gui.ui.window_manager._LOGGER.info", info_mock)

    press = MagicMock()
    press.type.return_value = QEvent.Type.MouseButtonPress
    press.button.return_value = Qt.MouseButton.LeftButton
    press.globalPosition.return_value.toPoint.return_value = QPoint(20, 30)

    assert manager._handle_title_bar_drag(press) is True
    assert info_mock.call_count >= 1


def test_detect_touching_edges_reports_expected_edges() -> None:
    """Edge detection helper should report contacts near available-geometry bounds."""

    frame = QRect(0, 0, 1920, 1080)
    available = QRect(0, 0, 1920, 1080)

    edges = FramelessWindowManager._detect_touching_edges(frame, available)

    assert set(edges) == {"left", "top", "right", "bottom"}


def test_detect_touching_edges_respects_tolerance() -> None:
    """Small offsets outside tolerance should not be classified as edge contact."""

    frame = QRect(20, 20, 1200, 800)
    available = QRect(0, 0, 1920, 1080)

    edges = FramelessWindowManager._detect_touching_edges(frame, available, tolerance=8)

    assert edges == ()


def test_trace_windows_snap_progress_logs_edge_contact(monkeypatch) -> None:
    """During active system move window edge contact should emit trace logs."""

    manager = FramelessWindowManager.__new__(FramelessWindowManager)
    manager._last_system_move_started_at = 999999.0
    manager._edge_snap_observed = False
    manager._drag_trace_id = 42

    window = MagicMock()
    window.frameGeometry.return_value = QRect(0, 0, 1920, 1080)
    window.screen.return_value = _FakeScreen(QRect(0, 0, 1920, 1080), dpr=1.0)
    window.isMaximized.return_value = False
    manager._window = window

    monkeypatch.setattr("iPhoto.gui.ui.window_manager.sys.platform", "win32")
    monkeypatch.setattr("iPhoto.gui.ui.window_manager.time.monotonic", lambda: 1000000.0)

    info_mock = MagicMock()
    monkeypatch.setattr("iPhoto.gui.ui.window_manager._LOGGER.info", info_mock)

    manager._trace_windows_snap_progress(QEvent.Type.Move)

    assert manager._edge_snap_observed is True
    assert any("snap_edge_contact" in str(call.args[1]) for call in info_mock.call_args_list if len(call.args) > 1)
