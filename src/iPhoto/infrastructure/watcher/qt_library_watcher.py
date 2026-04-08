"""Qt library watcher.

Encapsulates the ``QFileSystemWatcher`` implementation so that the
infrastructure concern (Qt-specific FS monitoring) is isolated from the
application-layer watcher service.

``LibraryManager`` owns a ``QtLibraryWatcher`` instance and delegates
watch-path management here while the
:class:`~iPhoto.application.services.library_watch_service.LibraryWatchService`
maintains the pause/resume depth and desired-path computation.
"""

from __future__ import annotations

from typing import Set

from PySide6.QtCore import QFileSystemWatcher

from ...utils.logging import get_logger

LOGGER = get_logger()


class QtLibraryWatcher:
    """Manage a ``QFileSystemWatcher`` instance for library directory monitoring."""

    def __init__(self, watcher: QFileSystemWatcher) -> None:
        self._watcher = watcher

    # ------------------------------------------------------------------
    # Path management
    # ------------------------------------------------------------------

    def current_paths(self) -> Set[str]:
        """Return the set of directories currently monitored."""
        return set(self._watcher.directories())

    def sync_paths(self, desired: Set[str]) -> None:
        """Add/remove paths so the watcher mirrors *desired*.

        Only the delta is sent to Qt to minimise unnecessary system calls.
        """
        current = self.current_paths()
        to_remove = [p for p in current if p not in desired]
        to_add = [p for p in desired if p not in current]
        if to_remove:
            self._watcher.removePaths(to_remove)
            LOGGER.debug("QtLibraryWatcher: removed %d paths", len(to_remove))
        if to_add:
            self._watcher.addPaths(to_add)
            LOGGER.debug("QtLibraryWatcher: added %d paths", len(to_add))

    def remove_all(self) -> None:
        """Remove all monitored paths."""
        dirs = self._watcher.directories()
        if dirs:
            self._watcher.removePaths(dirs)

    def is_empty(self) -> bool:
        """Return ``True`` when no paths are currently monitored."""
        return not bool(self._watcher.directories())


__all__ = ["QtLibraryWatcher"]
