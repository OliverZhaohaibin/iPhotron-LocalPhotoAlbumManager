from __future__ import annotations

from types import SimpleNamespace

from iPhoto.infrastructure.services import thumbnail_runtime_policy
from iPhoto.infrastructure.services.thumbnail_runtime_policy import (
    ThumbnailRuntimePolicy,
    resolve_physical_memory_bytes,
    speculative_thread_background_mode,
)


def test_windows_uses_bounded_budget_and_dedicated_guard_workers() -> None:
    policy = ThumbnailRuntimePolicy.detect(
        platform="win32",
        windows_probe=lambda: 16 * 1024**3,
    )

    assert policy.physical_memory_bytes == 16 * 1024**3
    assert policy.memory_limit_bytes == 384 * 1024**2
    assert policy.prefetch_max_workers == 4
    assert policy.guard_initial_workers == 2
    assert policy.guard_max_workers == 4
    assert policy.far_speculative_workers == 1
    assert policy.publish_max_items == 4
    assert policy.publish_budget_ms == 4.0
    assert policy.staging_limit == 24
    assert policy.guard_staging_limit == 32
    assert policy.far_staging_limit == 8
    assert policy.windows_low_memory_target_ratio == 0.60
    assert policy.guard_miss_ttl_seconds == 0.5
    assert policy.l1_replacement_threshold_ratio == 0.90
    assert policy.l1_replacement_target_ratio == 0.72
    assert policy.pixmap_pool_target_ratio == 0.72
    assert policy.urgent_pipeline_budget_ratio == 0.20
    assert policy.far_pipeline_budget_ratio == 0.05
    assert policy.low_memory_release_max_items == 2
    assert policy.low_memory_release_budget_ms == 1.0


def test_linux_uses_sysconf_and_two_guard_workers() -> None:
    values = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 262_144}
    policy = ThumbnailRuntimePolicy.detect(
        platform="linux",
        sysconf=values.__getitem__,
    )

    assert policy.physical_memory_bytes == 1024**3
    assert policy.memory_limit_bytes == 128 * 1024**2
    assert policy.prefetch_max_workers == 2
    assert policy.publish_max_items == 4
    assert policy.publish_budget_ms == 4.0


def test_macos_keeps_single_speculative_worker() -> None:
    values = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 2_097_152}
    policy = ThumbnailRuntimePolicy.detect(
        platform="darwin",
        sysconf=values.__getitem__,
    )

    assert policy.memory_limit_bytes == 384 * 1024**2
    assert policy.prefetch_max_workers == 1
    assert policy.l1_replacement_threshold_ratio == 0.95
    assert policy.l1_replacement_target_ratio == 0.88


def test_memory_probe_failure_falls_back_to_two_gib() -> None:
    physical = resolve_physical_memory_bytes(
        platform="win32",
        windows_probe=lambda: (_ for _ in ()).throw(OSError("failed")),
    )

    assert physical == 2 * 1024**3


def test_explicit_memory_limit_overrides_dynamic_budget() -> None:
    policy = ThumbnailRuntimePolicy.detect(
        platform="win32",
        windows_probe=lambda: 64 * 1024**3,
        memory_limit_mb=96,
    )

    assert policy.memory_limit_bytes == 96 * 1024**2


def test_windows_speculative_background_mode_sets_memory_priority(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    class _Api:
        argtypes = None
        restype = None

        def __init__(self, name, handler):
            self._name = name
            self._handler = handler

        def __call__(self, *args):
            return self._handler(*args)

    def _set_thread_priority(_handle, priority):
        calls.append(("priority", int(priority)))
        return 1

    def _set_thread_information(_handle, info_class, info, size):
        priority = int(info._obj.MemoryPriority)
        calls.append(("memory", priority))
        assert int(info_class) == 0
        assert int(size) > 0
        return 1

    kernel32 = SimpleNamespace(
        GetCurrentThread=_Api("GetCurrentThread", lambda: 1234),
        SetThreadPriority=_Api("SetThreadPriority", _set_thread_priority),
        SetThreadInformation=_Api("SetThreadInformation", _set_thread_information),
    )
    monkeypatch.setattr(
        thumbnail_runtime_policy.ctypes,
        "windll",
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    with speculative_thread_background_mode("win32"):
        pass

    assert calls == [
        ("priority", 0x00010000),
        ("memory", 1),
        ("memory", 5),
        ("priority", 0x00020000),
    ]
