from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QSurface
from PySide6.QtWidgets import QWidget

from iPhoto.gui import windows_fullscreen_composition as composition


def make_guard(qapp, monkeypatch, *, fullscreen=True, surface=None, hwnd=123):
    window = QWidget()
    handle = Mock()
    handle.surfaceType.return_value = surface or QSurface.SurfaceType.OpenGLSurface
    monkeypatch.setattr(window, "isFullScreen", lambda: fullscreen)
    monkeypatch.setattr(window, "windowHandle", lambda: handle)
    monkeypatch.setattr(window, "internalWinId", lambda: hwnd)
    guard = composition.WindowsFullscreenCompositionGuard(window)
    state = {"style": 0x90000000}
    api = Mock()
    api.get.side_effect = lambda _hwnd: state["style"]
    api.set.side_effect = lambda _hwnd, value: state.update(style=value)
    guard._api = api
    return window, handle, guard, api, state


def test_fullscreen_border_only_adds_the_native_bit_and_is_idempotent(qapp, monkeypatch):
    window, handle, guard, api, state = make_guard(qapp, monkeypatch)
    for kind in (QEvent.Type.WindowStateChange, QEvent.Type.Show, QEvent.Type.WinIdChange):
        assert guard.eventFilter(window, QEvent(kind)) is False
    api.set.assert_called_once_with(123, 0x90800000)
    assert state["style"] == 0x90800000
    # internalWinId is read-only; QWindow.winId could create a new native surface.
    handle.winId.assert_not_called()


@pytest.mark.parametrize(
    "surface", [QSurface.SurfaceType.MetalSurface, QSurface.SurfaceType.Direct3DSurface]
)
def test_other_backends_are_untouched(qapp, monkeypatch, surface):
    _, _, guard, api, _ = make_guard(qapp, monkeypatch, surface=surface)
    guard.apply_if_fullscreen()
    api.get.assert_not_called()


@pytest.mark.parametrize("fullscreen,hwnd", [(False, 123), (True, 0)])
def test_windowed_or_destroyed_handles_are_not_modified(qapp, monkeypatch, fullscreen, hwnd):
    _, handle, guard, api, _ = make_guard(qapp, monkeypatch, fullscreen=fullscreen, hwnd=hwnd)
    guard.apply_if_fullscreen()
    api.get.assert_not_called()
    handle.winId.assert_not_called()


def test_native_failure_does_not_interrupt_window_event(qapp, monkeypatch):
    window, _, guard, api, _ = make_guard(qapp, monkeypatch)
    api.set.side_effect = OSError(5, "denied")
    assert guard.eventFilter(window, QEvent(QEvent.Type.WindowStateChange)) is False
    assert not guard._applying


def test_exit_leaves_normal_style_restoration_to_qt(qapp, monkeypatch):
    window, _, guard, api, state = make_guard(qapp, monkeypatch)
    guard.apply_if_fullscreen()
    # Qt restores its previously saved normal style before WindowStateChange.
    state["style"] = 0x10000000
    monkeypatch.setattr(window, "isFullScreen", lambda: False)
    guard.eventFilter(window, QEvent(QEvent.Type.WindowStateChange))
    assert state["style"] == 0x10000000
    assert api.set.call_count == 1


def test_guard_requires_windows_and_explicit_opt_in(qapp, monkeypatch):
    window = QWidget()
    monkeypatch.setattr(composition, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.delenv("IPHOTO_WINDOWS_FULLSCREEN_BORDER", raising=False)
    assert composition.install_fullscreen_composition_guard(window) is None

    monkeypatch.setenv("IPHOTO_WINDOWS_FULLSCREEN_BORDER", "1")
    assert isinstance(
        composition.install_fullscreen_composition_guard(window),
        composition.WindowsFullscreenCompositionGuard,
    )
    monkeypatch.setattr(composition, "sys", SimpleNamespace(platform="darwin"))
    assert composition.install_fullscreen_composition_guard(window) is None


@pytest.mark.parametrize("pointer_size", [4, 8])
def test_native_style_api_has_correct_windows_abi_and_accepts_zero_previous_value(
    monkeypatch, pointer_size
):
    import ctypes

    get, set_value = Mock(return_value=0), Mock(return_value=0)
    library = SimpleNamespace(
        GetWindowLongPtrW=get,
        GetWindowLongW=get,
        SetWindowLongPtrW=set_value,
        SetWindowLongW=set_value,
    )
    ffi = SimpleNamespace(
        WinDLL=Mock(return_value=library),
        sizeof=lambda _: pointer_size,
        c_void_p=ctypes.c_void_p,
        c_int32=ctypes.c_int32,
        c_ssize_t=ctypes.c_ssize_t,
        set_last_error=Mock(),
        get_last_error=Mock(return_value=0),
    )
    monkeypatch.setattr(composition, "ctypes", ffi)
    api = composition._WindowStyles()
    assert get.argtypes == (ctypes.c_void_p, ctypes.c_int32)
    assert set_value.argtypes[-1] is (ctypes.c_ssize_t if pointer_size == 8 else ctypes.c_int32)
    api.set(123, 0x00800000)
    ffi.get_last_error.return_value = 5
    with pytest.raises(OSError):
        api.set(123, 0x00800000)
