import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QSurface
from PySide6.QtWidgets import QWidget

from iPhoto.gui import windowed_fullscreen as fullscreen
from iPhoto.gui.ui.widgets.gl_image_viewer.input_handler import InputEventHandler


def test_real_qt_round_trips_preserve_handle_geometry_and_fullscreen_semantics(qapp, monkeypatch):
    window = QWidget()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    window.resize(600, 400)
    window.move(60, 80)
    window.show()
    qapp.processEvents()
    original = window.geometry()
    hwnd = int(window.internalWinId())
    monkeypatch.setattr(fullscreen, "_use_windowed_fullscreen", lambda _: True)
    try:
        for _ in range(5):
            fullscreen.enter_media_fullscreen(window)
            qapp.processEvents()
            assert fullscreen.is_media_fullscreen(window)
            assert not window.isFullScreen()
            assert window.geometry() == window.screen().geometry().adjusted(0, 0, 0, 1)
            assert int(window.internalWinId()) == hwnd
            fullscreen.exit_media_fullscreen(window)
            qapp.processEvents()
            assert not fullscreen.is_media_fullscreen(window)
            assert window.geometry() == original
            assert int(window.internalWinId()) == hwnd
    finally:
        window.close()


def test_maximized_state_is_restored(qapp):
    window = QWidget()
    window.showMaximized()
    qapp.processEvents()
    controller = fullscreen.WindowedFullscreenController(window)
    try:
        controller.enter()
        assert fullscreen.is_media_fullscreen(window)
        controller.exit()
        assert window.isMaximized()
        assert not fullscreen.is_media_fullscreen(window)
    finally:
        window.close()


def test_double_click_exits_windowed_fullscreen(qapp):
    window = QWidget()
    window.setProperty(fullscreen._ACTIVE, True)
    exit_requested, enter_requested = Mock(), Mock()
    handler = InputEventHandler(
        Mock(),
        Mock(),
        on_replay_requested=Mock(),
        on_fullscreen_exit=exit_requested,
        on_fullscreen_toggle=enter_requested,
        on_cancel_auto_crop_lock=Mock(),
    )
    event = Mock()
    event.button.return_value = Qt.MouseButton.LeftButton
    assert handler.handle_double_click_with_window(event, window)
    exit_requested.assert_called_once()
    enter_requested.assert_not_called()


@pytest.mark.parametrize("override", [None, "", "auto", "1", "true", "on"])
def test_windows_opengl_uses_windowed_mode_without_collector_environment(monkeypatch, override):
    window = Mock()
    monkeypatch.setattr(fullscreen, "sys", SimpleNamespace(platform="win32"))
    if override is None:
        monkeypatch.delenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", raising=False)
    else:
        monkeypatch.setenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", override)
    window.windowHandle.return_value.surfaceType.return_value = QSurface.SurfaceType.OpenGLSurface
    assert fullscreen._use_windowed_fullscreen(window)


@pytest.mark.parametrize("override", ["0", "false", "off", "no"])
def test_native_comparison_explicitly_disables_windowed_mode(monkeypatch, override):
    window = Mock()
    monkeypatch.setattr(fullscreen, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", override)
    assert not fullscreen._use_windowed_fullscreen(window)


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_other_platforms_and_non_gl_surfaces_retain_native_fullscreen(monkeypatch, platform):
    window = Mock()
    monkeypatch.setattr(fullscreen, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", "1")
    if platform == "win32":
        window.windowHandle.return_value.surfaceType.return_value = (
            QSurface.SurfaceType.Direct3DSurface
        )
    else:
        window.windowHandle.return_value.surfaceType.return_value = (
            QSurface.SurfaceType.OpenGLSurface
        )
    assert not fullscreen._use_windowed_fullscreen(window)


def test_missing_native_handle_does_not_force_surface_creation(monkeypatch):
    window = Mock()
    window.windowHandle.return_value = None
    monkeypatch.setattr(fullscreen, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.delenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", raising=False)
    assert not fullscreen._use_windowed_fullscreen(window)
    window.winId.assert_not_called()


def test_normal_fullscreen_entry_logs_selected_policy_without_profiling(qapp, monkeypatch, caplog):
    import logging

    window = QWidget()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    window.show()
    qapp.processEvents()
    monkeypatch.setattr(fullscreen, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        window.windowHandle(), "surfaceType", lambda: QSurface.SurfaceType.OpenGLSurface
    )
    monkeypatch.delenv("IPHOTO_DETAIL_PROFILE", raising=False)
    monkeypatch.delenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", raising=False)
    try:
        with caplog.at_level(logging.INFO, logger=fullscreen.__name__):
            fullscreen.enter_media_fullscreen(window)
        assert "Media fullscreen strategy=windowed_overscan" in caplog.text
        assert "override='auto'" in caplog.text
        assert fullscreen.is_media_fullscreen(window)
        assert not window.isFullScreen()
        fullscreen.exit_media_fullscreen(window)
    finally:
        window.close()


def _pump(qapp):
    for _ in range(10):
        qapp.processEvents()


def _wait_for(qapp, predicate):
    from PySide6.QtTest import QTest

    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "Window transition did not reach its required terminal state within 2s"


def _shown_window(qapp, *, maximized=False):
    window = QWidget()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    window.setGeometry(60, 80, 500, 350)
    window.showMaximized() if maximized else window.show()
    _pump(qapp)
    return window


@pytest.mark.parametrize("maximized", [False, True])
def test_rejected_geometry_retries_without_resize_events_then_enters_native_once(
    qapp, monkeypatch, maximized
):
    window = _shown_window(qapp, maximized=maximized)
    original = window.geometry()
    controller = fullscreen.WindowedFullscreenController(window)
    setter = Mock()
    native = Mock(wraps=window.showFullScreen)
    monkeypatch.setattr(window, "setGeometry", setter)
    monkeypatch.setattr(window, "showFullScreen", native)
    try:
        controller.enter()
        _wait_for(
            qapp,
            lambda: (
                controller.phase
                in {
                    fullscreen.FullscreenPhase.NATIVE,
                    fullscreen.FullscreenPhase.INACTIVE,
                }
            ),
        )
        assert setter.call_count == 3
        native.assert_called_once()
        assert window.isFullScreen() and fullscreen.is_media_fullscreen(window)
        assert controller.phase == fullscreen.FullscreenPhase.NATIVE
        assert controller._pending_retry is None
        controller.reflow()
        assert setter.call_count == 3
        controller.exit()
        _pump(qapp)
        assert not fullscreen.is_media_fullscreen(window)
        assert window.geometry() == original
        assert window.isMaximized() == maximized
    finally:
        window.close()


def test_third_geometry_attempt_can_succeed_without_native_fallback(qapp, monkeypatch):
    window = _shown_window(qapp)
    controller = fullscreen.WindowedFullscreenController(window)
    real_set = window.setGeometry
    calls = []

    def set_geometry(rect):
        calls.append(rect)
        if len(calls) == 3:
            real_set(rect)

    monkeypatch.setattr(window, "setGeometry", set_geometry)
    native = Mock()
    monkeypatch.setattr(window, "showFullScreen", native)
    try:
        controller.enter()
        _pump(qapp)
        assert len(calls) == 3
        assert controller.phase == fullscreen.FullscreenPhase.WINDOWED
        assert controller.verification_count > 0
        assert controller._pending_retry is None
        native.assert_not_called()
        controller.exit()
    finally:
        window.close()


@pytest.mark.parametrize("maximized", [False, True])
def test_failed_native_fallback_restores_original_window_and_clears_fullscreen(
    qapp, monkeypatch, maximized
):
    window = _shown_window(qapp, maximized=maximized)
    original = window.geometry()
    controller = fullscreen.WindowedFullscreenController(window)
    monkeypatch.setattr(window, "setGeometry", Mock())
    native = Mock()
    monkeypatch.setattr(window, "showFullScreen", native)
    try:
        controller.enter()
        _wait_for(qapp, lambda: controller.phase == fullscreen.FullscreenPhase.INACTIVE)
        native.assert_called_once()
        assert controller.phase == fullscreen.FullscreenPhase.INACTIVE
        assert window.property(fullscreen._ACTIVE) is False
        assert not fullscreen.is_media_fullscreen(window)
        assert window.isMaximized() == maximized
        assert window.geometry() == original
        assert controller._pending_retry is None
    finally:
        window.close()


def test_old_retry_cannot_change_a_new_entry(qapp, monkeypatch):
    window = _shown_window(qapp)
    controller = fullscreen.WindowedFullscreenController(window)
    real_set = window.setGeometry
    setter = Mock()
    monkeypatch.setattr(window, "setGeometry", setter)
    try:
        controller.enter()
        old_retry = controller._pending_retry
        assert old_retry is not None
        controller.exit()
        monkeypatch.setattr(window, "setGeometry", real_set)
        controller.enter()
        count = controller._attempts
        old_retry()
        assert controller._attempts == count
        assert controller.phase == fullscreen.FullscreenPhase.WINDOWED
        controller.exit()
    finally:
        window.close()


def test_screen_change_invalidates_queued_geometry_retry(qapp, monkeypatch):
    window = _shown_window(qapp)
    controller = fullscreen.WindowedFullscreenController(window)
    geometry = Mock(return_value=QRect(-1536, 0, 1536, 960))
    monkeypatch.setattr(window, "screen", lambda: SimpleNamespace(geometry=geometry))
    setter = Mock()
    monkeypatch.setattr(window, "setGeometry", setter)
    try:
        controller.enter()
        old_retry = controller._pending_retry
        geometry.return_value = QRect(-1280, 0, 1280, 800)
        controller.eventFilter(window, QEvent(QEvent.Type.DevicePixelRatioChange))
        assert controller._attempts == 1
        before = setter.call_count
        old_retry()
        assert setter.call_count == before
        setter.assert_called_with(QRect(-1280, 0, 1280, 801))
        controller.exit()
    finally:
        window.close()


def test_minimized_window_is_not_repositioned(qapp, monkeypatch):
    window = QWidget()
    controller = fullscreen.WindowedFullscreenController(window)
    window.setProperty(fullscreen._ACTIVE, True)
    controller.phase = fullscreen.FullscreenPhase.ENTERING_WINDOWED
    monkeypatch.setattr(window, "isMinimized", lambda: True)
    setter = Mock()
    monkeypatch.setattr(window, "setGeometry", setter)
    controller.reflow()
    setter.assert_not_called()


def test_system_maximize_supersedes_pending_native_fallback(qapp, monkeypatch):
    window = _shown_window(qapp)
    controller = fullscreen.WindowedFullscreenController(window)
    monkeypatch.setattr(window, "setGeometry", Mock())
    monkeypatch.setattr(window, "showFullScreen", Mock())
    try:
        controller.enter()
        controller._dispatch_retry()
        controller._dispatch_retry()
        assert controller.phase == fullscreen.FullscreenPhase.ENTERING_NATIVE
        stale_verification = controller._pending_retry
        window.showMaximized()
        stale_verification()
        _pump(qapp)
        assert window.isMaximized()
        assert controller.phase == fullscreen.FullscreenPhase.INACTIVE
        assert not fullscreen.is_media_fullscreen(window)
    finally:
        window.close()


def test_accepted_close_cancels_pending_retry_without_reopening_window(qapp, monkeypatch):
    window = _shown_window(qapp)
    controller = fullscreen.WindowedFullscreenController(window)
    monkeypatch.setattr(window, "setGeometry", Mock())
    native = Mock()
    monkeypatch.setattr(window, "showFullScreen", native)
    controller.enter()
    stale_retry = controller._pending_retry
    window.close()
    stale_retry()
    _pump(qapp)
    assert controller.phase == fullscreen.FullscreenPhase.INACTIVE
    assert not window.isVisible()
    native.assert_not_called()


def test_ignored_close_keeps_fullscreen_session_active(qapp):
    class RejectClose(QWidget):
        def closeEvent(self, event):
            event.ignore()

    window = RejectClose()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    window.setGeometry(60, 80, 500, 350)
    window.show()
    _pump(qapp)
    controller = fullscreen.WindowedFullscreenController(window)
    try:
        controller.enter()
        assert window.close() is False
        _pump(qapp)
        assert window.isVisible()
        assert controller.phase == fullscreen.FullscreenPhase.WINDOWED
        assert fullscreen.is_media_fullscreen(window)
    finally:
        controller.exit()
        window.hide()


@pytest.mark.parametrize("native_succeeds", [True, False])
def test_delayed_maximized_restore_cannot_cancel_bounded_entry(qapp, monkeypatch, native_succeeds):
    """Reproduce a late maximize notification while the first restore is pending."""
    from PySide6.QtGui import QWindowStateChangeEvent

    window = _shown_window(qapp, maximized=True)
    controller = fullscreen.WindowedFullscreenController(window)
    original = window.geometry()
    setter = Mock()
    native = Mock(wraps=window.showFullScreen) if native_succeeds else Mock()
    monkeypatch.setattr(window, "setGeometry", setter)
    monkeypatch.setattr(window, "showFullScreen", native)
    try:
        controller.enter()
        # Simulate WM state echoing the pre-entry maximized state after showNormal.
        with monkeypatch.context() as delayed:
            delayed.setattr(window, "isMaximized", lambda: True)
            controller.eventFilter(window, QWindowStateChangeEvent(Qt.WindowState.WindowNoState))
            assert controller.phase == fullscreen.FullscreenPhase.ENTERING_WINDOWED
            assert window.property(fullscreen._ACTIVE) is True
        _wait_for(
            qapp,
            lambda: (
                controller.phase
                in {
                    fullscreen.FullscreenPhase.NATIVE,
                    fullscreen.FullscreenPhase.INACTIVE,
                }
            ),
        )
        assert setter.call_count == 3
        native.assert_called_once()
        assert fullscreen.is_media_fullscreen(window) == native_succeeds
        if native_succeeds:
            controller.exit()
        _wait_for(qapp, lambda: window.isMaximized() and window.geometry() == original)
        assert window.isMaximized()
        assert window.geometry() == original
        assert controller._pending_retry is None
    finally:
        window.close()


def test_shell_hint_precedes_overscan_and_clears_on_system_exit(qapp, monkeypatch):
    window = _shown_window(qapp)
    controller = fullscreen.WindowedFullscreenController(window)
    events = []
    real_set = window.setGeometry
    monkeypatch.setattr(
        fullscreen, "mark_fullscreen_window", lambda hwnd, flag: events.append((hwnd, flag))
    )
    monkeypatch.setattr(
        window, "setGeometry", lambda rect: (events.append("geometry"), real_set(rect))
    )
    hwnd = int(window.internalWinId())
    try:
        controller.enter()
        assert events.index((hwnd, True)) < events.index("geometry")
        controller.eventFilter(window, QEvent(QEvent.Type.Show))
        controller.eventFilter(window, QEvent(QEvent.Type.WindowActivate))
        assert events.count((hwnd, True)) >= 3
        window.showMaximized()
        _wait_for(qapp, lambda: controller.phase == fullscreen.FullscreenPhase.INACTIVE)
        assert events[-1] == (hwnd, False)
        assert window.isMaximized()
        controller.exit()
        assert window.isMaximized()
    finally:
        window.close()


def test_shell_failure_does_not_recreate_window_or_break_exit(qapp, monkeypatch):
    window = _shown_window(qapp)
    hwnd = int(window.internalWinId())
    original = window.geometry()
    controller = fullscreen.WindowedFullscreenController(window)
    mark = Mock(return_value=False)
    monkeypatch.setattr(fullscreen, "mark_fullscreen_window", mark)
    try:
        controller.enter()
        assert controller.phase == fullscreen.FullscreenPhase.WINDOWED
        controller.exit()
        assert window.geometry() == original
        assert int(window.internalWinId()) == hwnd
        assert mark.call_args.args == (hwnd, False)
    finally:
        window.close()


def test_delayed_maximize_during_native_failure_still_restores_original(qapp, monkeypatch):
    from PySide6.QtGui import QWindowStateChangeEvent

    window = _shown_window(qapp, maximized=True)
    original = window.geometry()
    controller = fullscreen.WindowedFullscreenController(window)
    monkeypatch.setattr(window, "setGeometry", Mock())
    monkeypatch.setattr(window, "showFullScreen", Mock())
    try:
        controller.enter()
        for _ in range(3):
            controller._dispatch_retry()
        assert controller.phase == fullscreen.FullscreenPhase.ENTERING_NATIVE
        with monkeypatch.context() as delayed:
            delayed.setattr(window, "isMaximized", lambda: True)
            controller.eventFilter(window, QWindowStateChangeEvent(Qt.WindowState.WindowNoState))
            assert controller.phase == fullscreen.FullscreenPhase.ENTERING_NATIVE
        _wait_for(
            qapp,
            lambda: (
                controller.phase == fullscreen.FullscreenPhase.INACTIVE
                and window.isMaximized()
                and window.geometry() == original
            ),
        )
        window.showFullScreen.assert_called_once()
        assert window.property(fullscreen._ACTIVE) is False
    finally:
        window.close()
