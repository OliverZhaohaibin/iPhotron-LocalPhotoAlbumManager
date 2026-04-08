"""QFileSystemWatcher wrapper for monitoring library directory changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class FileSystemWatcherMixin:
    """Mixin providing file-system watch management for LibraryManager."""

    def pause_watcher(self) -> None:
        """Temporarily suppress change notifications during internal writes."""

        # Increment the suspension depth so nested pause calls continue to be
        # reference-counted.  The debounce timer is stopped on the first pause
        # to ensure that an earlier notification does not race with the write we
        # are about to perform.
        self._watch_suspend_depth += 1
        if self._watch_suspend_depth == 1 and self._debounce.isActive():
            self._debounce.stop()

    def resume_watcher(self) -> None:
        """Re-enable change notifications once protected writes have finished."""

        if self._watch_suspend_depth == 0:
            return
        self._watch_suspend_depth -= 1

    def _on_directory_changed(self, path: str) -> None:
        # Skip notifications while we are in the middle of an internally
        # triggered write such as a manifest save.  The associated UI components
        # already know about those updates, so reacting to the file-system event
        # would only cause redundant reloads.
        if self._watch_suspend_depth > 0:
            return

        # ``QFileSystemWatcher`` emits plain strings.  Queue a debounced refresh
        # whenever a change notification arrives so the sidebar reflects
        # external edits without thrashing the filesystem.
        self._debounce.start()

    def _rebuild_watches(self) -> None:
        current = set(self._watcher.directories())
        desired: set[str] = set()
        if self._root is not None:
            desired.add(str(self._root))
            desired.update(str(node.path) for node in self._albums)
        remove = [path for path in current if path not in desired]
        if remove:
            self._watcher.removePaths(remove)
        add = [path for path in desired if path not in current]
        if add:
            self._watcher.addPaths(add)
