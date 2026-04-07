"""Library watch service.

Owns watcher pause/resume depth tracking and watch-path set computation.
The actual ``QFileSystemWatcher`` management stays in the Qt layer
(``LibraryManager`` / ``FileSystemWatcherMixin``); this service provides
the *pure-Python* business logic so it can be tested without Qt.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set


class LibraryWatchService:
    """Track watcher suspension depth and compute desired watch paths."""

    def __init__(self) -> None:
        self._suspend_depth: int = 0

    # ------------------------------------------------------------------
    # Pause / resume
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """Increment the suspension counter (reference-counted)."""
        self._suspend_depth += 1

    def resume(self) -> None:
        """Decrement the suspension counter.  No-op when already at 0."""
        if self._suspend_depth > 0:
            self._suspend_depth -= 1

    def is_first_pause(self) -> bool:
        """Return ``True`` when this is the first (outermost) pause call.

        Use this instead of comparing ``suspend_depth() == 1`` in callers
        to avoid coupling to the internal depth representation.
        """
        return self._suspend_depth == 1

    def is_suspended(self) -> bool:
        """Return ``True`` when at least one pause call is outstanding."""
        return self._suspend_depth > 0

    def suspend_depth(self) -> int:
        """Return the current suspension depth."""
        return self._suspend_depth

    # ------------------------------------------------------------------
    # Watch path computation
    # ------------------------------------------------------------------

    def compute_desired_paths(
        self,
        library_root: Optional[Path],
        album_paths: List[Path],
    ) -> Set[str]:
        """Return the set of directory strings that should be watched.

        Includes the library root (when set) and all top-level album directories.
        """
        desired: Set[str] = set()
        if library_root is not None:
            desired.add(str(library_root))
        for path in album_paths:
            desired.add(str(path))
        return desired


__all__ = ["LibraryWatchService"]
