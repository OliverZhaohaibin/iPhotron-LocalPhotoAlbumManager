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


class _MemoryPriorityInformation(ctypes.Structure):
    _fields_ = [("MemoryPriority", ctypes.c_ulong)]


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


_LOW_MEMORY_RESOURCE_NOTIFICATION = 0
_low_memory_notification_handle: int | None = None


def windows_low_memory_resource_active() -> bool:
    """Best-effort non-blocking Windows low-memory resource check."""

    global _low_memory_notification_handle
    if not sys.platform.lower().startswith("win"):
        return False
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if _low_memory_notification_handle is None:
            create_notification = kernel32.CreateMemoryResourceNotification
            create_notification.argtypes = [ctypes.c_int]
            create_notification.restype = ctypes.c_void_p
            handle = create_notification(_LOW_MEMORY_RESOURCE_NOTIFICATION)
            _low_memory_notification_handle = int(handle or 0)
        if not _low_memory_notification_handle:
            return False
        query_notification = kernel32.QueryMemoryResourceNotification
        query_notification.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        query_notification.restype = ctypes.c_int
        state = ctypes.c_int(0)
        if not query_notification(
            ctypes.c_void_p(_low_memory_notification_handle),
            ctypes.byref(state),
        ):
            return False
        return bool(state.value)
    except (AttributeError, OSError, ctypes.ArgumentError, ValueError):
        return False


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
    *,
    platform: str | None = None,
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
    prefetch_miss_ttl_seconds: float = 2.0
    guard_miss_ttl_seconds: float = 0.5
    far_speculative_workers: int = 1
    guard_initial_workers: int = 1
    guard_max_workers: int = 1
    guard_staging_limit: int = 12
    far_staging_limit: int = 8
    windows_low_memory_target_ratio: float = 0.65
    windows_low_memory_probe_interval_ms: int = 250
    l1_replacement_threshold_ratio: float = 0.95
    l1_replacement_target_ratio: float = 0.88

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
        publish_max_items = 2
        publish_budget_ms = 3.0
        guard_initial_workers = 1
        guard_max_workers = 1
        guard_staging_limit = 12
        far_staging_limit = 8
        windows_low_memory_target_ratio = 0.65
        far_speculative_workers = 1
        l1_replacement_threshold_ratio = 0.95
        l1_replacement_target_ratio = 0.88
        if platform_name.startswith("win"):
            prefetch_workers = 4
            publish_max_items = 4
            publish_budget_ms = 4.0
            guard_initial_workers = 2
            guard_max_workers = 4
            guard_staging_limit = 32
            far_staging_limit = 8
            windows_low_memory_target_ratio = 0.60
            l1_replacement_threshold_ratio = 0.90
            l1_replacement_target_ratio = 0.72
        elif platform_name.startswith("linux"):
            prefetch_workers = 2
            publish_max_items = 4
            publish_budget_ms = 4.0
            guard_initial_workers = 1
            guard_max_workers = 2
            guard_staging_limit = 18
            far_staging_limit = 8
        else:
            prefetch_workers = 1
        return cls(
            platform=platform_name,
            physical_memory_bytes=physical,
            memory_limit_bytes=resolve_l1_memory_limit_bytes(
                physical,
                memory_limit_mb,
                platform=platform_name,
            ),
            prefetch_max_workers=prefetch_workers,
            publish_max_items=publish_max_items,
            publish_budget_ms=publish_budget_ms,
            staging_limit=max(
                24 if platform_name.startswith("win") else 8,
                prefetch_workers * 4,
            ),
            far_speculative_workers=far_speculative_workers,
            guard_initial_workers=guard_initial_workers,
            guard_max_workers=guard_max_workers,
            guard_staging_limit=guard_staging_limit,
            far_staging_limit=far_staging_limit,
            windows_low_memory_target_ratio=windows_low_memory_target_ratio,
            l1_replacement_threshold_ratio=l1_replacement_threshold_ratio,
            l1_replacement_target_ratio=l1_replacement_target_ratio,
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
    memory_priority_started = False
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
    if kernel32 is not None and thread_handle is not None:
        try:
            set_thread_information = kernel32.SetThreadInformation
            set_thread_information.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_ulong,
            ]
            set_thread_information.restype = ctypes.c_int
            low_memory_priority = _MemoryPriorityInformation(1)
            memory_priority_started = bool(
                set_thread_information(
                    thread_handle,
                    0,
                    ctypes.byref(low_memory_priority),
                    ctypes.sizeof(low_memory_priority),
                )
            )
        except (AttributeError, OSError, ctypes.ArgumentError):
            memory_priority_started = False
    try:
        yield
    finally:
        if kernel32 is not None and thread_handle is not None and memory_priority_started:
            try:
                normal_memory_priority = _MemoryPriorityInformation(5)
                kernel32.SetThreadInformation(
                    thread_handle,
                    0,
                    ctypes.byref(normal_memory_priority),
                    ctypes.sizeof(normal_memory_priority),
                )
            except (AttributeError, OSError, ctypes.ArgumentError):
                pass
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
    "windows_low_memory_resource_active",
]
