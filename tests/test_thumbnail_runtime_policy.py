from __future__ import annotations

from iPhoto.infrastructure.services.thumbnail_runtime_policy import (
    ThumbnailRuntimePolicy,
    resolve_physical_memory_bytes,
)


def test_windows_uses_global_memory_probe_and_four_predictive_workers() -> None:
    policy = ThumbnailRuntimePolicy.detect(
        platform="win32",
        windows_probe=lambda: 16 * 1024**3,
    )

    assert policy.physical_memory_bytes == 16 * 1024**3
    assert policy.memory_limit_bytes == 1536 * 1024**2
    assert policy.prefetch_max_workers == 4
    assert policy.publish_max_items == 4
    assert policy.publish_budget_ms == 5.0


def test_linux_uses_sysconf_and_three_predictive_workers() -> None:
    values = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 262_144}
    policy = ThumbnailRuntimePolicy.detect(
        platform="linux",
        sysconf=values.__getitem__,
    )

    assert policy.physical_memory_bytes == 1024**3
    assert policy.memory_limit_bytes == 512 * 1024**2
    assert policy.prefetch_max_workers == 3
    assert policy.publish_max_items == 4
    assert policy.publish_budget_ms == 5.0


def test_macos_keeps_single_speculative_worker() -> None:
    values = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 2_097_152}
    policy = ThumbnailRuntimePolicy.detect(
        platform="darwin",
        sysconf=values.__getitem__,
    )

    assert policy.memory_limit_bytes == 384 * 1024**2
    assert policy.prefetch_max_workers == 1


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
