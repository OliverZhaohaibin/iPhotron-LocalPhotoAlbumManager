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


_NOT_FOUND: object = object()  # Sentinel for cache misses


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
        """Record *path* as potentially requiring a forced reload."""
        key = self._key(path)
        if key is None:
            return
        self._stale_album_roots[key] = path

    def consume_forced_reload(self, path: Path) -> bool:
        """Return ``True`` and consume the stale marker when *path* is stale."""
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
    # Move rels computation
    # ------------------------------------------------------------------

    def compute_move_rels(
        self,
        moved_pairs: List[Tuple[Path, Path]],
        library_root: Optional[Path],
        source_root: Path,
        destination_root: Path,
    ) -> Tuple[List[str], List[str]]:
        """Return ``(removed_rels, added_rels)`` for a completed move operation.

        Each ``rel`` is the path relative to the library root (or album root
        when no library root is set).  Pairs that cannot be made relative are
        silently skipped.
        """
        removed: List[str] = []
        added: List[str] = []

        src_base = library_root if library_root else source_root
        dst_base = library_root if library_root else destination_root

        try:
            src_base_norm = src_base.resolve()
        except OSError:
            src_base_norm = src_base

        try:
            dst_base_norm = dst_base.resolve()
        except OSError:
            dst_base_norm = dst_base

        for original, target in moved_pairs:
            try:
                removed.append(original.resolve().relative_to(src_base_norm).as_posix())
            except (OSError, ValueError):
                pass
            try:
                added.append(target.resolve().relative_to(dst_base_norm).as_posix())
            except (OSError, ValueError):
                pass

        return removed, added

    # ------------------------------------------------------------------
    # Refresh targets computation
    # ------------------------------------------------------------------

    def compute_refresh_targets(
        self,
        moved_pairs: List[Tuple[Path, Path]],
        source_root: Path,
        destination_root: Path,
        current_root: Optional[Path],
        library_root: Optional[Path],
        *,
        source_ok: bool,
        destination_ok: bool,
    ) -> Dict[str, Tuple[Path, bool]]:
        """Compute which album roots need an index/links refresh after a move.

        Returns a ``{path_key: (path, should_restart)}`` mapping.
        ``should_restart`` is ``True`` when *path* matches the currently open
        album so the view should reload.
        """
        from ...application.policies.library_scope_policy import LibraryScopePolicy

        scope = LibraryScopePolicy()
        refresh_targets: Dict[str, Tuple[Path, bool]] = {}
        blocked_restarts: Set[str] = set()

        def _record(path: Optional[Path], *, allow_restart: bool = True) -> None:
            if path is None:
                return
            k = self._key(path) or str(path)
            self.mark_stale(path)
            if not allow_restart:
                blocked_restarts.add(k)
            should_restart = bool(
                allow_restart
                and k not in blocked_restarts
                and current_root is not None
                and scope.paths_equal(current_root, path)
            )
            existing = refresh_targets.get(k)
            if existing is None or (not existing[1] and should_restart):
                refresh_targets[k] = (path, should_restart)

        if source_ok:
            _record(source_root, allow_restart=False)
        if destination_ok:
            _record(destination_root)

        if library_root is not None:
            additional_roots = self.collect_album_roots_from_pairs(moved_pairs, library_root)
            for extra_root in additional_roots:
                _record(extra_root)

            # Check if the library root itself is touched by this move.
            touched_library = False
            if source_ok and scope.paths_equal(source_root, library_root):
                touched_library = True
            if destination_ok and scope.paths_equal(destination_root, library_root):
                touched_library = True
            if not touched_library:
                for original, target in moved_pairs:
                    if scope.is_within_library(original, library_root) or scope.is_within_library(
                        target, library_root
                    ):
                        touched_library = True
                        break
            if touched_library:
                _record(library_root)

        return refresh_targets

    # ------------------------------------------------------------------
    # Album root location
    # ------------------------------------------------------------------

    def locate_album_root(
        self,
        start: Path,
        library_root: Path,
    ) -> Optional[Path]:
        """Walk upwards from *start* to find the nearest album root."""
        try:
            candidate = start.resolve()
        except (OSError, ValueError):
            candidate = start

        key = str(candidate)
        cached = self._album_root_cache.get(key, _NOT_FOUND)
        if cached is not _NOT_FOUND:
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
        """Return the set of album roots touched by *pairs* within *library_root*."""
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
        """Return album roots that should be rescanned after a restore operation."""
        from ...application.policies.library_scope_policy import LibraryScopePolicy

        scope = LibraryScopePolicy()
        unique: Dict[str, Path] = {}

        for _, destination in moved_pairs:
            album_root = Path(destination).parent
            try:
                album_norm = album_root.resolve()
            except (OSError, ValueError):
                album_norm = album_root

            if library_root is not None and not scope.is_within_library(album_norm, library_root):
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
