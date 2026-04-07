"""Library scope policy.

Owns all rules for determining whether a path belongs to the current
library and for reasoning about cross-library moves and restores.

This centralises scope-checking logic that was previously duplicated
across ``library_update_service.py``, ``app.py``, and various use cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class LibraryScopePolicy:
    """Determine whether paths and operations are within a library's scope."""

    # ------------------------------------------------------------------
    # Membership checks
    # ------------------------------------------------------------------

    def is_within_library(self, path: Path, library_root: Path) -> bool:
        """Return ``True`` when *path* is equal to or a descendant of *library_root*."""
        try:
            candidate = path.resolve()
            root = library_root.resolve()
        except OSError:
            candidate = path
            root = library_root
        if candidate == root:
            return True
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def is_cross_library_move(
        self,
        source: Path,
        target: Path,
        library_root: Path,
    ) -> bool:
        """Return ``True`` when *source* and *target* are in different library scopes.

        A move is considered cross-library when exactly one of the two paths is
        inside *library_root*.
        """
        source_in = self.is_within_library(source, library_root)
        target_in = self.is_within_library(target, library_root)
        return source_in != target_in

    # ------------------------------------------------------------------
    # Library-relative helpers
    # ------------------------------------------------------------------

    def library_relative_path(self, path: Path, library_root: Path) -> Optional[str]:
        """Return the POSIX library-relative path for *path*, or ``None``.

        Returns ``None`` when *path* is not inside *library_root* or when the
        path resolution fails.
        """
        try:
            rel = path.resolve().relative_to(library_root.resolve()).as_posix()
            return rel if rel not in (".", "") else None
        except (ValueError, OSError):
            pass
        try:
            rel = path.relative_to(library_root).as_posix()
            return rel if rel not in (".", "") else None
        except ValueError:
            return None


__all__ = ["LibraryScopePolicy"]
