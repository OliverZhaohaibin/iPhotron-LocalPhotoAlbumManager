"""Memory usage monitor with configurable thresholds and callbacks.

Provides a lightweight interface for tracking process RSS memory and
triggering warning / critical callbacks when configurable limits are
exceeded.  Designed to be polled periodically (e.g. from a timer or
background thread) rather than running its own loop.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, List

LOGGER = logging.getLogger(__name__)

# 1 MiB / 1 GiB in bytes — handy for callers constructing thresholds.
MiB: int = 1 << 20
GiB: int = 1 << 30


@dataclass
class MemorySnapshot:
    """Point-in-time memory reading."""

    rss_bytes: int = 0
    """Resident Set Size in bytes."""

    @property
    def rss_mib(self) -> float:
        return self.rss_bytes / MiB

    @property
    def rss_gib(self) -> float:
        return self.rss_bytes / GiB


# Type alias for memory-threshold callbacks.
MemoryCallback = Callable[[MemorySnapshot], None]


class MemoryMonitor:
    """Track process memory and fire callbacks on threshold breach.

    Parameters
    ----------
    warning_bytes:
        When RSS exceeds this value, ``on_warning`` callbacks fire.
    critical_bytes:
        When RSS exceeds this value, ``on_critical`` callbacks fire.
    """

    def __init__(
        self,
        warning_bytes: int = 1 * GiB,
        critical_bytes: int = 2 * GiB,
    ) -> None:
        self._warning_bytes = warning_bytes
        self._critical_bytes = critical_bytes

        self._on_warning: List[MemoryCallback] = []
        self._on_critical: List[MemoryCallback] = []

        self._lock = threading.Lock()
        self._last_snapshot = MemorySnapshot()
        self._warning_fired = False
        self._critical_fired = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_warning_callback(self, cb: MemoryCallback) -> None:
        with self._lock:
            self._on_warning.append(cb)

    def add_critical_callback(self, cb: MemoryCallback) -> None:
        with self._lock:
            self._on_critical.append(cb)

    @property
    def warning_bytes(self) -> int:
        return self._warning_bytes

    @property
    def critical_bytes(self) -> int:
        return self._critical_bytes

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def check(self) -> MemorySnapshot:
        """Sample current RSS and invoke callbacks if thresholds crossed.

        Returns the latest :class:`MemorySnapshot`.
        """
        snap = self._read_rss()
        with self._lock:
            self._last_snapshot = snap

            if snap.rss_bytes >= self._critical_bytes:
                if not self._critical_fired:
                    self._critical_fired = True
                    LOGGER.warning(
                        "Memory CRITICAL: %.1f MiB (threshold %.1f MiB)",
                        snap.rss_mib,
                        self._critical_bytes / MiB,
                    )
                    for cb in self._on_critical:
                        try:
                            cb(snap)
                        except Exception:
                            LOGGER.exception("Error in critical memory callback")
            else:
                self._critical_fired = False

            if snap.rss_bytes >= self._warning_bytes:
                if not self._warning_fired:
                    self._warning_fired = True
                    LOGGER.warning(
                        "Memory WARNING: %.1f MiB (threshold %.1f MiB)",
                        snap.rss_mib,
                        self._warning_bytes / MiB,
                    )
                    for cb in self._on_warning:
                        try:
                            cb(snap)
                        except Exception:
                            LOGGER.exception("Error in warning memory callback")
            else:
                self._warning_fired = False

        return snap

    @property
    def last_snapshot(self) -> MemorySnapshot:
        with self._lock:
            return self._last_snapshot

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _read_rss() -> MemorySnapshot:
        """Read the current process RSS from ``/proc/self/status`` or
        :mod:`resource` as a cross-platform fallback.
        """
        rss = 0
        try:
            # Fast path: Linux
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # Value is in kB
                        rss = int(line.split()[1]) * 1024
                        break
        except OSError:
            try:
                import resource
                # ru_maxrss is in KB on Linux, bytes on macOS
                usage = resource.getrusage(resource.RUSAGE_SELF)
                rss = usage.ru_maxrss
                if os.uname().sysname == "Darwin":
                    pass  # already bytes
                else:
                    rss *= 1024  # KB → bytes
            except Exception:
                pass
        return MemorySnapshot(rss_bytes=rss)
