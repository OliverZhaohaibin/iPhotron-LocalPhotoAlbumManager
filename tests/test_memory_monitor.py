"""Tests for MemoryMonitor — process memory tracking with thresholds."""

from __future__ import annotations

import pytest

from iPhoto.infrastructure.services.memory_monitor import (
    GiB,
    MiB,
    MemoryMonitor,
    MemorySnapshot,
)


class TestMemorySnapshot:
    def test_rss_mib(self):
        snap = MemorySnapshot(rss_bytes=100 * MiB)
        assert snap.rss_mib == pytest.approx(100.0)

    def test_rss_gib(self):
        snap = MemorySnapshot(rss_bytes=2 * GiB)
        assert snap.rss_gib == pytest.approx(2.0)

    def test_zero(self):
        snap = MemorySnapshot(rss_bytes=0)
        assert snap.rss_mib == 0.0
        assert snap.rss_gib == 0.0


class TestMemoryMonitor:
    def test_check_returns_snapshot(self):
        mon = MemoryMonitor()
        snap = mon.check()
        assert isinstance(snap, MemorySnapshot)
        assert snap.rss_bytes >= 0

    def test_last_snapshot_updated_after_check(self):
        mon = MemoryMonitor()
        assert mon.last_snapshot.rss_bytes == 0
        snap = mon.check()
        assert mon.last_snapshot.rss_bytes == snap.rss_bytes

    def test_warning_callback_fires(self):
        """Force a warning by setting threshold to 0 bytes."""
        calls: list[MemorySnapshot] = []
        mon = MemoryMonitor(warning_bytes=0, critical_bytes=100 * GiB)
        mon.add_warning_callback(calls.append)
        mon.check()
        assert len(calls) == 1
        assert calls[0].rss_bytes >= 0

    def test_critical_callback_fires(self):
        calls: list[MemorySnapshot] = []
        mon = MemoryMonitor(warning_bytes=0, critical_bytes=0)
        mon.add_critical_callback(calls.append)
        mon.check()
        assert len(calls) == 1

    def test_warning_fires_only_once_until_reset(self):
        calls: list[MemorySnapshot] = []
        mon = MemoryMonitor(warning_bytes=0, critical_bytes=100 * GiB)
        mon.add_warning_callback(calls.append)
        mon.check()
        mon.check()
        # Should fire only once (sticky until rss drops below threshold)
        assert len(calls) == 1

    def test_no_callback_when_below_threshold(self):
        calls: list[MemorySnapshot] = []
        mon = MemoryMonitor(warning_bytes=100 * GiB, critical_bytes=200 * GiB)
        mon.add_warning_callback(calls.append)
        mon.check()
        assert len(calls) == 0

    def test_threshold_properties(self):
        mon = MemoryMonitor(warning_bytes=512 * MiB, critical_bytes=2 * GiB)
        assert mon.warning_bytes == 512 * MiB
        assert mon.critical_bytes == 2 * GiB

    def test_callback_exception_does_not_propagate(self):
        def _bad_cb(snap: MemorySnapshot) -> None:
            raise ValueError("boom")

        mon = MemoryMonitor(warning_bytes=0, critical_bytes=100 * GiB)
        mon.add_warning_callback(_bad_cb)
        # Should not raise
        snap = mon.check()
        assert snap.rss_bytes >= 0
