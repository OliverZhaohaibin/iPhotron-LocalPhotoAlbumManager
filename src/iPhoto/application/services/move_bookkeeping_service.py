"""Move bookkeeping service.

Owns the domain rules for tracking which albums are stale after a move/restore
operation, locating the album root for an arbitrary path, and computing which
albums need to be rescanned after a bulk-restore.

This logic was previously embedded inline in
``gui/services/library_update_service.py``.  Extracting it to the application
layer makes it testable without Qt and removes business rules from the UI
service.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ...config import WORK_DIR_NAME
from ...utils.logging import get_logger

LOGGER = get_logger()


class MoveBookkeepingService:
    """Track stale albums and locate album roots after move/restore operations.

    All methods are pure-Python and Qt-free so they can be unit-tested without
    a running Qt application.
    """

    def __init__(self) -> None:
        # Paths that have been modified and may need a forced reload.
        self._stale_album_roots: Dict[str, Path] = {}
        # Cache of start-path → album-root resolution results.
        self._album_root_cache: Dict[str, Optional[Path]] = {}

    # ------------------------------------------------------------------
    # Stale album tracking
    # ------------------------------------------------------------------

    def mark_stale(self, path: Path) -> None:
        """Record *path* as potentially requiring a forced reload.

        The normalised POSIX string of the resolved path is used as the key so
        that logically identical paths with different representations are
        de-duplicated.
        """
        key = self._key(path)
        if key is None:
            return
        self._stale_album_roots[key] = path

    def consume_forced_reload(self, path: Path) -> bool:
        """Return ``True`` and consume the stale marker when *path* is stale.

        After returning ``True`` the marker is removed so subsequent calls for
        the same path return ``False``.
        """
        key = self._key(path)
        if key is None or key not in self._stale_album_roots:
            return False
        self._stale_album_roots.pop(key, None)
        return True

    def reset(self) -> None:
        """Drop all stale markers and the album-root resolution cache."""
        self._stale_album_roots.clear()
        self._album_root_cache.clear()

    # ------------------------------------------------------------------
    # Album root location
    # ------------------------------------------------------------------

    def locate_album_root(
        self,
        start: Path,
        library_root: Path,
    ) -> Optional[Path]:
        """Walk upwards from *start* to find the nearest album root.

        An album root is identified by the presence of the ``WORK_DIR_NAME``
        (``.iPhoto``) directory.  The search stops at *library_root*.

        Results are cached so that repeated look-ups for the same starting
        path are cheap.
        """
        try:
            candidate = start.resolve()
        except (OSError, ValueError):
            candidate = start

        key = str(candidate)
        cached = self._album_root_cache.get(key, ...)
        if cached is not ...:
            return cached  # type: ignore[return-value]

        try:
            library_norm = library_root.resolve()
        except (OSError, ValueError):
            library_norm = library_root

        visited: List[Path] = []
        current = candidate
        album_root: Optional[Path] = None

        while True:
            visited.append(current)
            if (current / WORK_DIR_NAME).exists():
                album_root = current
                break
            if current == library_norm or current.parent == current:
                album_root = None
                break
            current = current.parent

        for entry in visited:
            self._album_root_cache[str(entry)] = album_root

        return album_root

    def collect_album_roots_from_pairs(
        self,
        pairs: List[Tuple[Path, Path]],
        library_root: Path,
    ) -> Set[Path]:
        """Return the set of album roots touched by *pairs* within *library_root*.

        For each (original, target) path pair, both the original and target
        directories are checked.  Only album roots that lie inside
        *library_root* are included in the result.
        """
        if not pairs:
            return set()

        try:
            library_norm = library_root.resolve()
        except (OSError, ValueError):
            library_norm = library_root

        affected: Set[Path] = set()
        for original, target in pairs:
            for candidate in (original, target):
                album_root = self.locate_album_root(candidate.parent, library_norm)
                if album_root is not None:
                    affected.add(album_root)
        return affected

    # ------------------------------------------------------------------
    # Restore rescan target computation
    # ------------------------------------------------------------------

    def compute_restore_rescan_targets(
        self,
        moved_pairs: List[Tuple[Path, Path]],
        library_root: Optional[Path],
    ) -> List[Path]:
        """Return album roots that should be rescanned after a restore operation.

        Each file restored from the trash lands in ``destination``.  Its
        *parent* is the album directory that needs to be rescanned.  Only
        destinations that fall inside *library_root* are included.
        """
        if library_root is None:
            library_root_norm: Optional[Path] = None
        else:
            try:
                library_root_norm = library_root.resolve()
            except (OSError, ValueError):
                library_root_norm = library_root

        unique: Dict[str, Path] = {}
        for _, destination in moved_pairs:
            album_root = Path(destination).parent
            try:
                album_norm = album_root.resolve()
            except (OSError, ValueError):
                album_norm = album_root

            if library_root_norm is not None:
                try:
                    album_norm.relative_to(library_root_norm)
                except ValueError:
                    LOGGER.debug(
                        "compute_restore_rescan_targets: %s outside library; skipping",
                        album_norm,
                    )
                    continue

            key = str(album_norm)
            if key not in unique:
                unique[key] = album_root

        return list(unique.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, path: Path) -> Optional[str]:
        try:
            return str(path.resolve())
        except (OSError, ValueError):
            return str(path)


__all__ = ["MoveBookkeepingService"]
