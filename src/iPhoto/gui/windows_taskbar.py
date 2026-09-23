"""Tell Explorer about windowed fullscreen without changing the HWND or renderer.

https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-itaskbarlist2-markfullscreenwindow
"""

from __future__ import annotations

import ctypes
import logging
import sys
import uuid

from .detail_profile import emit_detail_event

_LOGGER = logging.getLogger(__name__)
_HRESULT = ctypes.c_int32
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int32
_HWND = ctypes.c_void_p
_RPC_E_CHANGED_MODE = 0x80010106


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


_CLSID_TASKBAR = _GUID.from_buffer_copy(uuid.UUID("56fdf344-fd6d-11d0-958a-006097c9a090").bytes_le)
_IID_TASKBAR2 = _GUID.from_buffer_copy(uuid.UUID("602d4995-b13a-429b-a66e-1935e44f4317").bytes_le)


def _check(result: int, operation: str) -> None:
    if result & 0x80000000:
        raise OSError(f"{operation} failed: HRESULT 0x{result & 0xFFFFFFFF:08x}")


def _method(pointer, slot, result, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[slot])


def _mark(hwnd: int, active: bool) -> None:
    # Balance only our own COM initialization; Qt may already own this apartment.
    # Keep the interface local to this call/thread, releasing it before uninitializing.
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = (ctypes.c_void_p, _DWORD)
    ole.CoInitializeEx.restype = _HRESULT
    ole.CoUninitialize.argtypes = ()
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = (
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        _DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    ole.CoCreateInstance.restype = _HRESULT
    initialized = ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    if initialized & 0xFFFFFFFF != _RPC_E_CHANGED_MODE:
        _check(initialized, "CoInitializeEx")
    pointer = ctypes.c_void_p()
    try:
        _check(
            ole.CoCreateInstance(
                ctypes.byref(_CLSID_TASKBAR),
                None,
                1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(_IID_TASKBAR2),
                ctypes.byref(pointer),
            ),
            "CoCreateInstance(ITaskbarList2)",
        )
        if not pointer.value:
            raise OSError("ITaskbarList2 returned a null interface")
        _check(_method(pointer, 3, _HRESULT)(pointer), "ITaskbarList.HrInit")
        _check(
            _method(pointer, 8, _HRESULT, _HWND, _BOOL)(pointer, hwnd, int(active)),
            "ITaskbarList2.MarkFullscreenWindow",
        )
    finally:
        try:
            if pointer.value:
                _method(pointer, 2, _DWORD)(pointer)  # IUnknown.Release
        finally:
            if not initialized & 0x80000000:
                ole.CoUninitialize()


def mark_fullscreen_window(hwnd: int, active: bool) -> bool:
    """Best-effort Shell hint, never topmost/activation/driver manipulation."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        _mark(hwnd, active)
    except (OSError, AttributeError, RuntimeError, ValueError) as error:
        _LOGGER.warning("Windows fullscreen Shell hint failed: %s", error)
        emit_detail_event(
            "fullscreen_shell_mark",
            generation=0,
            active=active,
            applied=False,
            native_id=hwnd,
            error=str(error),
        )
        return False
    emit_detail_event(
        "fullscreen_shell_mark", generation=0, active=active, applied=True, native_id=hwnd
    )
    return True
