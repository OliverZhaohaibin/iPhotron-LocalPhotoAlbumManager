from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_FALLBACK_PHYSICAL_MEMORY_BYTES = 2 * _GIB


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_physical_memory_bytes() -> int:
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    global_memory_status = kernel32.GlobalMemoryStatusEx
    global_memory_status.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
    global_memory_status.restype = ctypes.c_int
    if not global_memory_status(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return int(status.ullTotalPhys)


def resolve_physical_memory_bytes(
    *,
    platform: str | None = None,
    sysconf: Callable[[str], int] | None = None,
    windows_probe: Callable[[], int] | None = None,
) -> int:
    """Return physical RAM with a conservative cross-platform fallback."""

    platform_name = (platform or sys.platform).lower()
    try:
        if platform_name.startswith("win"):
            physical = int((windows_probe or _windows_physical_memory_bytes)())
        else:
            query = sysconf or os.sysconf
            physical = int(query("SC_PAGE_SIZE")) * int(query("SC_PHYS_PAGES"))
        if physical > 0:
            return physical
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return _FALLBACK_PHYSICAL_MEMORY_BYTES


def resolve_l1_memory_limit_bytes(
    physical_memory_bytes: int,
    memory_limit_mb: int | None = None,
) -> int:
    if memory_limit_mb is not None:
        return max(16, int(memory_limit_mb)) * _MIB
    proposed = int(max(0, physical_memory_bytes) * 0.075)
    return max(128 * _MIB, min(384 * _MIB, proposed))


@dataclass(frozen=True, slots=True)
class ThumbnailRuntimePolicy:
    platform: str
    physical_memory_bytes: int
    memory_limit_bytes: int
    visible_workers: int = 2
    prefetch_max_workers: int = 1
    publish_max_items: int = 2
    publish_budget_ms: float = 3.0
    staging_limit: int = 12
    prefetch_sample_size: int = 16
    prefetch_slow_p95_ms: float = 40.0
    prefetch_cancel_rate: float = 0.25
    prefetch_backoff_seconds: float = 2.0
    prefetch_miss_ttl_seconds: float = 2.0
    visible_queue_wait_p95_ms: float = 12.0
    far_speculative_workers: int = 1

    @classmethod
    def detect(
        cls,
        *,
        memory_limit_mb: int | None = None,
        platform: str | None = None,
        sysconf: Callable[[str], int] | None = None,
        windows_probe: Callable[[], int] | None = None,
    ) -> ThumbnailRuntimePolicy:
        platform_name = (platform or sys.platform).lower()
        physical = resolve_physical_memory_bytes(
            platform=platform_name,
            sysconf=sysconf,
            windows_probe=windows_probe,
        )
        if platform_name.startswith("win"):
            prefetch_workers = 3
        elif platform_name.startswith("linux"):
            prefetch_workers = 2
        else:
            prefetch_workers = 1
        return cls(
            platform=platform_name,
            physical_memory_bytes=physical,
            memory_limit_bytes=resolve_l1_memory_limit_bytes(physical, memory_limit_mb),
            prefetch_max_workers=prefetch_workers,
            staging_limit=max(8, prefetch_workers * 4),
        )


@contextmanager
def speculative_thread_background_mode(platform: str) -> Iterator[None]:
    """Lower Windows speculative CPU, disk and memory scheduling priority."""

    if not platform.lower().startswith("win"):
        yield
        return

    kernel32 = None
    thread_handle = None
    background_started = False
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        get_current_thread = kernel32.GetCurrentThread
        get_current_thread.restype = ctypes.c_void_p
        set_thread_priority = kernel32.SetThreadPriority
        set_thread_priority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_thread_priority.restype = ctypes.c_int
        thread_handle = get_current_thread()
        background_started = bool(set_thread_priority(thread_handle, 0x00010000))
    except (AttributeError, OSError, ctypes.ArgumentError):
        kernel32 = None
        thread_handle = None
    try:
        yield
    finally:
        if kernel32 is not None and thread_handle is not None and background_started:
            try:
                kernel32.SetThreadPriority(thread_handle, 0x00020000)
            except (AttributeError, OSError, ctypes.ArgumentError):
                pass


__all__ = [
    "ThumbnailRuntimePolicy",
    "resolve_l1_memory_limit_bytes",
    "resolve_physical_memory_bytes",
    "speculative_thread_background_mode",
]
