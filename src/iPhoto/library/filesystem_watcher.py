"""QFileSystemWatcher wrapper for monitoring library directory changes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..bootstrap.library_scan_service import LibraryScanService
from ..config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE

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
        if self._watch_suspend_depth == 1:
            if self._debounce.isActive():
                self._debounce.stop()
            self._pending_watch_paths.clear()

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
        if path:
            self._pending_watch_paths.add(Path(path))
        self._debounce.start()

    def _on_watcher_debounce_timeout(self) -> None:
        """Refresh the tree and scan changed watcher scopes through the session."""

        previous_album_paths = self._known_album_paths()
        pending_paths = set(self._pending_watch_paths)
        self._pending_watch_paths.clear()
        self._refresh_tree()
        pending_paths = self._expand_new_album_watch_paths(
            pending_paths,
            previous_album_paths,
        )
        self._start_watcher_scans(pending_paths)

    def _start_watcher_scans(self, paths: set[Path]) -> None:
        if self._root is None or not paths:
            return

        scan_roots = self._dedupe_watch_scan_roots(paths)
        self._queue_watcher_scan_roots(scan_roots)

    def _queue_watcher_scan_roots(self, scan_roots: list[Path]) -> None:
        """Queue watcher-triggered scan scopes without collapsing album filters."""

        if not scan_roots:
            return

        queued = {
            self._watch_scan_key(scan_root)
            for scan_root in self._watch_scan_queue
        }
        active_root = getattr(self, "_live_scan_root", None)
        for scan_root in scan_roots:
            key = self._watch_scan_key(scan_root)
            if key in queued:
                continue
            if active_root is not None and self._paths_equal(active_root, scan_root):
                continue
            self._watch_scan_queue.append(scan_root)
            queued.add(key)

        self._start_next_watcher_scan()

    def _start_next_watcher_scan(self) -> None:
        if self._root is None:
            self._watch_scan_queue.clear()
            return
        if getattr(self, "_current_scanner_worker", None) is not None:
            return

        scan_service = getattr(self, "scan_service", None)
        if scan_service is None:
            scan_service = LibraryScanService(self._root)

        while self._watch_scan_queue:
            scan_root = self._watch_scan_queue.pop(0)
            if not scan_root.exists() or not scan_root.is_dir():
                continue
            try:
                include, exclude = scan_service.scan_filters(scan_root)
            except Exception:
                include, exclude = list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE)
            self.start_scanning(scan_root, include, exclude)
            return

    def _on_watcher_scan_finished(self, _root: Path, _success: bool) -> None:
        self._start_next_watcher_scan()

    @staticmethod
    def _watch_scan_key(path: Path) -> str:
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def _known_album_paths(self) -> set[Path]:
        """Return the currently discovered album directories."""

        return {Path(node.path) for node in getattr(self, "_nodes", {}).values()}

    def _expand_new_album_watch_paths(
        self,
        paths: set[Path],
        previous_album_paths: set[Path],
    ) -> set[Path]:
        """Scan newly discovered albums instead of their parent watch path."""

        if not paths:
            return paths

        previous_keys = {
            self._watch_scan_key(path)
            for path in previous_album_paths
        }
        new_album_paths = [
            path
            for path in sorted(self._known_album_paths(), key=lambda item: item.as_posix())
            if self._watch_scan_key(path) not in previous_keys
        ]
        if not new_album_paths:
            return paths

        expanded = set(paths)
        for pending_path in paths:
            additions = [
                album_path
                for album_path in new_album_paths
                if self._path_covers(pending_path, album_path)
            ]
            if not additions:
                continue
            expanded.discard(pending_path)
            expanded.update(additions)
        return expanded

    @staticmethod
    def _path_covers(root: Path, candidate: Path) -> bool:
        try:
            root_resolved = root.resolve()
            candidate_resolved = candidate.resolve()
        except OSError:
            return False
        return (
            candidate_resolved == root_resolved
            or root_resolved in candidate_resolved.parents
        )

    def _dedupe_watch_scan_roots(self, paths: set[Path]) -> list[Path]:
        """Return minimal existing directory scopes for watcher-triggered scans."""

        if self._root is None:
            return []

        root_resolved = self._root.resolve()
        candidates: list[Path] = []
        for raw_path in sorted(paths, key=lambda item: item.as_posix()):
            try:
                path = raw_path.resolve()
            except OSError:
                continue
            if not path.exists():
                continue
            if not path.is_dir():
                path = path.parent
            try:
                path.relative_to(root_resolved)
            except ValueError:
                continue
            candidates.append(path)

        if len(candidates) > 1:
            candidates = [candidate for candidate in candidates if candidate != root_resolved]

        deduped: list[Path] = []
        for candidate in candidates:
            if any(
                existing == candidate or existing in candidate.parents
                for existing in deduped
            ):
                continue
            deduped = [
                existing
                for existing in deduped
                if not (candidate == existing or candidate in existing.parents)
            ]
            deduped.append(candidate)
        return deduped

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
