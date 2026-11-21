"""Exercise the actual COM ABI against an in-memory ITaskbarList2 vtable."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from iPhoto.gui import windows_taskbar as taskbar


@pytest.mark.parametrize("initialized", [0, 1, -2147417850])  # S_OK, S_FALSE, CHANGED_MODE
@pytest.mark.parametrize("failure", [None, "create", "init", "mark"])
def test_com_lifetime_abi_and_errors(monkeypatch, initialized, failure):
    calls = []
    factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", factory, raising=False)
    error = -2147467259

    @factory(ctypes.c_uint32, ctypes.c_void_p)
    def release(this):
        calls.append("release")
        return 0

    @factory(ctypes.c_int32, ctypes.c_void_p)
    def init(this):
        calls.append("init")
        return error if failure == "init" else 0

    @factory(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32)
    def mark(this, hwnd, active):
        calls.append((hwnd, active))
        return error if failure == "mark" else 0

    table = (ctypes.c_void_p * 9)()
    table[2], table[3], table[8] = [
        ctypes.cast(f, ctypes.c_void_p).value for f in (release, init, mark)
    ]
    table_pointer = ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))
    interface = ctypes.pointer(table_pointer)

    def create(clsid, outer, context, iid, out):
        assert bytes(clsid._obj) == bytes(taskbar._CLSID_TASKBAR)
        assert bytes(iid._obj) == bytes(taskbar._IID_TASKBAR2)
        assert context == 1 and outer is None
        calls.append("create")
        if failure != "create":
            out._obj.value = ctypes.cast(interface, ctypes.c_void_p).value
        return error if failure == "create" else 0

    ole = SimpleNamespace(
        CoInitializeEx=Mock(return_value=initialized),
        CoCreateInstance=Mock(side_effect=create),
        CoUninitialize=Mock(side_effect=lambda: calls.append("uninitialize")),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=ole), raising=False)
    hwnd = 0x123456789 if ctypes.sizeof(ctypes.c_void_p) == 8 else 12345
    if failure:
        with pytest.raises(OSError, match="HRESULT"):
            taskbar._mark(hwnd, True)
    else:
        taskbar._mark(hwnd, True)
        assert (hwnd, 1) in calls
        taskbar._mark(hwnd, False)
        assert (hwnd, 0) in calls
    assert calls.count("release") == (0 if failure == "create" else (1 if failure else 2))
    assert ole.CoUninitialize.call_count == (0 if initialized < 0 else (1 if failure else 2))
    if initialized >= 0:
        assert calls[-1] == "uninitialize"
    assert ctypes.sizeof(taskbar._GUID) == 16
    assert ctypes.sizeof(taskbar._HRESULT) == ctypes.sizeof(taskbar._BOOL) == 4


def test_failed_com_initialization_is_not_uninitialized(monkeypatch):
    ole = SimpleNamespace(
        CoInitializeEx=Mock(return_value=-2147467259),
        CoCreateInstance=Mock(),
        CoUninitialize=Mock(),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=ole), raising=False)
    with pytest.raises(OSError):
        taskbar._mark(123, True)
    ole.CoCreateInstance.assert_not_called()
    ole.CoUninitialize.assert_not_called()


def test_unavailable_shell_is_reported_without_propagating(monkeypatch, caplog):
    monkeypatch.setattr(taskbar, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(taskbar, "_mark", Mock(side_effect=OSError("Shell unavailable")))
    event = Mock()
    monkeypatch.setattr(taskbar, "emit_detail_event", event)
    assert taskbar.mark_fullscreen_window(123, True) is False
    assert event.call_args.kwargs["applied"] is False
    assert "Shell unavailable" in caplog.text


def test_non_windows_does_not_load_com(monkeypatch):
    monkeypatch.setattr(taskbar, "sys", SimpleNamespace(platform="linux"))
    mark = Mock()
    monkeypatch.setattr(taskbar, "_mark", mark)
    assert taskbar.mark_fullscreen_window(123, True) is False
    mark.assert_not_called()


@pytest.mark.skipif(taskbar.sys.platform != "win32", reason="requires Windows Explorer COM")
def test_real_shell_hint_keeps_existing_window_and_qt_state(qapp):
    from PySide6.QtWidgets import QWidget

    window = QWidget()
    window.show()
    qapp.processEvents()
    hwnd = int(window.internalWinId())
    state, flags = window.windowState(), window.windowFlags()
    try:
        assert taskbar.mark_fullscreen_window(hwnd, True)
        assert int(window.internalWinId()) == hwnd
        assert window.windowState() == state
        assert window.windowFlags() == flags
    finally:
        cleared = taskbar.mark_fullscreen_window(hwnd, False)
        window.close()
    assert cleared
