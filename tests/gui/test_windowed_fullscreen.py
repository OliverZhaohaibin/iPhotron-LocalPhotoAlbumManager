from types import SimpleNamespace
from unittest.mock import Mock

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


def test_windowed_mode_is_opt_in_and_gl_only(monkeypatch):
    window = Mock()
    monkeypatch.setattr(fullscreen, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.delenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", raising=False)
    assert not fullscreen._use_windowed_fullscreen(window)
    monkeypatch.setenv("IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN", "1")
    window.windowHandle.return_value.surfaceType.return_value = QSurface.SurfaceType.OpenGLSurface
    assert fullscreen._use_windowed_fullscreen(window)
    window.windowHandle.return_value.surfaceType.return_value = QSurface.SurfaceType.Direct3DSurface
    assert not fullscreen._use_windowed_fullscreen(window)
    monkeypatch.setattr(fullscreen, "sys", SimpleNamespace(platform="darwin"))
    assert not fullscreen._use_windowed_fullscreen(window)


def test_resize_rejection_is_bounded_and_dpi_target_change_gets_new_attempts(qapp, monkeypatch):
    window = QWidget()
    controller = fullscreen.WindowedFullscreenController(window)
    window.setProperty(fullscreen._ACTIVE, True)
    geometry = Mock(return_value=QRect(-1536, 0, 1536, 960))
    monkeypatch.setattr(window, "screen", lambda: SimpleNamespace(geometry=geometry))
    setter = Mock()
    monkeypatch.setattr(window, "setGeometry", setter)
    for _ in range(20):
        controller.eventFilter(window, QEvent(QEvent.Type.Resize))
    assert setter.call_count == 3
    assert controller.verification_count == 0
    geometry.return_value = QRect(-1280, 0, 1280, 800)
    controller.eventFilter(window, QEvent(QEvent.Type.DevicePixelRatioChange))
    assert setter.call_count == 4
    setter.assert_called_with(QRect(-1280, 0, 1280, 801))


def test_minimized_window_is_not_repositioned(qapp, monkeypatch):
    window = QWidget()
    controller = fullscreen.WindowedFullscreenController(window)
    window.setProperty(fullscreen._ACTIVE, True)
    monkeypatch.setattr(window, "isMinimized", lambda: True)
    setter = Mock()
    monkeypatch.setattr(window, "setGeometry", setter)
    controller.reflow()
    setter.assert_not_called()
