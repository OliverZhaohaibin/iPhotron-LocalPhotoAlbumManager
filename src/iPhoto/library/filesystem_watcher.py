"""QFileSystemWatcher wrapper for monitoring library directory changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class FileSystemWatcherMixin:
    """Mixin providing file-system watch management for LibraryManager.

    All suspension-depth tracking and watch-path computation is now delegated
    to ``LibraryWatchService`` and ``QtLibraryWatcher`` (Phase 2).  The mixin
    only contains the Qt-event wiring that must live on the combined QObject.
    """

    def pause_watcher(self) -> None:
        """Temporarily suppress change notifications during internal writes."""

        # Delegate depth tracking to the application-layer service.
        self._watch_service.pause()
        # Stop any pending debounce on the first pause so an earlier
        # notification does not race with the write we are about to perform.
        if self._watch_service.suspend_depth() == 1 and self._debounce.isActive():
            self._debounce.stop()

    def resume_watcher(self) -> None:
        """Re-enable change notifications once protected writes have finished."""

        self._watch_service.resume()

    def _on_directory_changed(self, path: str) -> None:
        # Skip notifications while we are in the middle of an internally
        # triggered write such as a manifest save.
        if self._watch_service.is_suspended():
            return

        # Queue a debounced refresh whenever a change notification arrives.
        self._debounce.start()

    def _rebuild_watches(self) -> None:
        desired = self._watch_service.compute_desired_paths(
            self._root,
            [node.path for node in self._albums],
        )
        self._qt_watcher.sync_paths(desired)
