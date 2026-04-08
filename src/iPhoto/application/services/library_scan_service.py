"""Library scan service.

Owns scan-state management, worker coordination, and geotagged-cache
invalidation that were previously embedded in ``ScanCoordinatorMixin``.

``LibraryManager`` delegates scan operations here while maintaining its
existing public API for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional

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
    # Scan start/stop decision helpers
    # ------------------------------------------------------------------

    def should_skip_start(self, root: Path) -> bool:
        """Return ``True`` when a scan for *root* is already active."""
        if not self._scan_in_progress or self._scan_root is None:
            return False
        try:
            return self._scan_root.resolve() == root.resolve()
        except OSError:
            return self._scan_root == root

    # ------------------------------------------------------------------
    # Scan-finished bookkeeping
    # ------------------------------------------------------------------

    def on_scan_finished(
        self,
        root: Path,
        library_root: Optional[Path],
        *,
        pair_callback: Optional[Callable[[Path, Optional[Path]], None]] = None,
    ) -> None:
        """Handle the completion of a scan.

        Clears scan state and invokes the ``pair_callback`` when provided.
        The callback accepts ``(root, library_root)`` and is responsible for
        persisting Live Photo pairings.  Keeping the callback injected lets
        callers (the thin Qt mixin) supply the concrete implementation while
        this service stays Qt-free.
        """
        self.mark_stopped()
        if pair_callback is not None:
            try:
                pair_callback(root, library_root)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "LibraryScanService: pair_callback raised after scan of %s: %s", root, exc
                )

    def on_scan_error(self, root: Path) -> None:  # noqa: ARG002
        """Handle a scan error by clearing the scan state."""
        self.mark_stopped()

    # ------------------------------------------------------------------
    # Live scan rows: db-backed read helper
    # ------------------------------------------------------------------

    def read_live_rows_from_store(
        self,
        query_root: Path,
        library_root: Path,
    ) -> List[Dict]:
        """Read index rows from the global store scoped to *query_root*.

        Returns an empty list on any error (best-effort).
        """
        try:
            from ...cache.index_store import get_global_repository

            store = get_global_repository(library_root)
            try:
                album_path = query_root.resolve().relative_to(library_root.resolve()).as_posix()
            except (OSError, ValueError):
                try:
                    album_path = query_root.relative_to(library_root).as_posix()
                except ValueError:
                    album_path = None

            if album_path in (None, "", "."):
                rows = store.read_all(sort_by_date=True, filter_hidden=True)
            else:
                rows = store.read_album_assets(
                    album_path,
                    include_subalbums=True,
                    sort_by_date=True,
                    filter_hidden=True,
                )
            return [dict(row) for row in rows]
        except Exception:  # noqa: BLE001
            LOGGER.debug("LibraryScanService: failed to read live scan rows", exc_info=True)
            return []

    def resolve_live_query_root(
        self,
        scan_root: Path,
        relative_to: Optional[Path],
    ) -> Optional[Path]:
        """Return the effective query root for a live-scan read.

        When *relative_to* is provided and is an ancestor or descendant of
        *scan_root*, the more specific of the two is returned.  Returns
        ``None`` when the two paths are unrelated.
        """
        if relative_to is None:
            return scan_root
        try:
            rel_root_res = relative_to.resolve()
            scan_root_res = scan_root.resolve()
        except OSError:
            return None

        if scan_root_res == rel_root_res:
            return scan_root
        if rel_root_res in scan_root_res.parents:
            return scan_root
        if scan_root_res in rel_root_res.parents:
            return relative_to
        return None

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
