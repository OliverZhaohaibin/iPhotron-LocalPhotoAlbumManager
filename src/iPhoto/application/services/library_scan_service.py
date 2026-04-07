"""Library scan service.

Owns scan-state management, worker coordination, and geotagged-cache
invalidation that were previously embedded in ``ScanCoordinatorMixin``.

``LibraryManager`` delegates scan operations here while maintaining its
existing public API for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ...utils.logging import get_logger

LOGGER = get_logger()


class LibraryScanService:
    """Coordinate background scanning for a library.

    This is a **pure Python** service with no Qt dependency.  It stores scan
    state and exposes helpers so that ``LibraryManager`` (the QObject) can
    delegate without mixing Qt concerns into the business logic.
    """

    def __init__(self) -> None:
        self._scan_root: Optional[Path] = None
        self._scan_in_progress: bool = False

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    def is_scanning(self) -> bool:
        """Return ``True`` when a scan is currently running."""
        return self._scan_in_progress

    def current_scan_root(self) -> Optional[Path]:
        """Return the root currently being scanned, or ``None``."""
        return self._scan_root

    def is_scanning_path(self, path: Path) -> bool:
        """Return ``True`` when *path* is covered by the active scan."""
        if not self._scan_root:
            return False
        try:
            target = path.resolve()
            scan_root = self._scan_root.resolve()
            return target == scan_root or scan_root in target.parents
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def mark_started(self, root: Path) -> None:
        """Record that a scan for *root* has started."""
        self._scan_root = root
        self._scan_in_progress = True
        LOGGER.debug("LibraryScanService: scan started for %s", root)

    def mark_stopped(self) -> None:
        """Record that the active scan has finished or been cancelled."""
        self._scan_root = None
        self._scan_in_progress = False
        LOGGER.debug("LibraryScanService: scan stopped")

    # ------------------------------------------------------------------
    # Geotagged cache invalidation helper
    # ------------------------------------------------------------------

    def should_invalidate_geo_cache(
        self,
        scan_root: Path,
        geo_cache_root: Optional[Path],
    ) -> bool:
        """Return ``True`` when the geo cache should be dropped after this scan."""
        if geo_cache_root is None:
            return False
        try:
            return scan_root.resolve() == geo_cache_root.resolve()
        except OSError:
            return scan_root == geo_cache_root


__all__ = ["LibraryScanService"]
