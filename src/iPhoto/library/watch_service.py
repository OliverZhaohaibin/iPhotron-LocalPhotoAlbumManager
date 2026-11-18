"""Generation-aware background library watching and tree snapshots."""

from __future__ import annotations

import stat
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QFileSystemWatcher,
    QObject,
    QThread,
    QTimer,
    Signal,
    Slot,
)

from ..config import (
    ALBUM_MANIFEST_NAMES,
    ALL_WORK_DIR_NAMES,
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    EXPORT_DIR_NAME,
    RECENTLY_DELETED_DIR_NAME,
)
from ..errors import IPhotoError
from ..utils.jsonio import read_json
from ..utils.pathutils import should_include
from .tree import AlbumNode


_RESERVED_NAMES = frozenset(
    name.casefold()
    for name in (*ALL_WORK_DIR_NAMES, RECENTLY_DELETED_DIR_NAME, EXPORT_DIR_NAME)
)
_ROOT_CONTROL_FILES = frozenset({ALBUM_MANIFEST_NAMES[0], ".iphoto.album", "links.json"})


@dataclass(frozen=True, slots=True)
class LibraryWatchRequest:
    generation: int
    root: Path
    paths: tuple[Path, ...]
    polling: bool
    scan_filters: Callable[[Path], tuple[list[str], list[str]]] | None = None


@dataclass(frozen=True, slots=True)
class LibraryWatchResult:
    generation: int
    changed_paths: tuple[Path, ...]
    albums: tuple[AlbumNode, ...]
    warning: str | None = None
    root_media_changed: bool = False


def _describe_album(path: Path) -> tuple[str, bool]:
    for manifest_name in ALBUM_MANIFEST_NAMES:
        manifest = path / manifest_name
        if not manifest.exists():
            continue
        try:
            data = read_json(manifest)
        except Exception:
            return path.name, True
        title = data.get("title") if isinstance(data, dict) else None
        return str(title or path.name), True
    marker = path / ".iphoto.album"
    return path.name, marker.exists()


def _snapshot_entry_is_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except FileNotFoundError:
        if path.is_symlink():
            return False
        # A disappeared entry may be a renamed album. Retry the snapshot instead
        # of publishing an omission that could discard its persisted state.
        raise
    except NotADirectoryError:
        raise
    except OSError:
        return False


def _tree_snapshot(entries: list[Path]) -> tuple[AlbumNode, ...]:
    nodes: list[AlbumNode] = []
    for entry in entries:
        if entry.name.casefold() in _RESERVED_NAMES:
            continue
        if not _snapshot_entry_is_dir(entry):
            continue
        title, has_manifest = _describe_album(entry)
        nodes.append(AlbumNode(entry, 1, title, has_manifest))
        children = sorted(entry.iterdir(), key=lambda item: item.name.casefold())
        for child in children:
            if child.name.casefold() in _RESERVED_NAMES or not _snapshot_entry_is_dir(child):
                continue
            child_title, child_manifest = _describe_album(child)
            nodes.append(AlbumNode(child, 2, child_title, child_manifest))
    return tuple(nodes)


def _root_media_signature(
    root: Path,
    entries: list[Path],
    previous: dict[str, tuple[int, int, int]],
    include: list[str],
    exclude: list[str],
) -> dict[str, tuple[int, int, int]]:
    """Track direct media independently of internal directory writes."""

    signature: dict[str, tuple[int, int, int]] = {}
    for entry in entries:
        if entry.suffix.lower() == ".ipo" or entry.name.casefold() in _ROOT_CONTROL_FILES:
            continue
        if not should_include(entry, include, exclude, root=root):
            continue
        try:
            info = entry.stat()
        except FileNotFoundError:
            continue
        except OSError:
            if entry.name in previous:
                signature[entry.name] = previous[entry.name]
            continue
        if stat.S_ISREG(info.st_mode):
            signature[entry.name] = (info.st_ino, info.st_size, info.st_mtime_ns)
    return signature


class _LibraryWatchWorker(QObject):
    resultReady = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._watcher: QFileSystemWatcher | None = None
        self._batch_timer: QTimer | None = None
        self._poll_timer: QTimer | None = None
        self._request: LibraryWatchRequest | None = None
        self._pending_adds: list[str] = []
        self._poll_signature: tuple[tuple[str, int, int], ...] | None = None
        self._root_media: dict[str, tuple[int, int, int]] | None = None
        self._albums: tuple[AlbumNode, ...] | None = None
        self._pending_tree_paths: set[Path] = set()

    @Slot()
    def initialize(self) -> None:
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._add_next_batch)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(3000)
        self._poll_timer.timeout.connect(self._poll)

    @Slot(object)
    def configure(self, request: object) -> None:
        if not isinstance(request, LibraryWatchRequest):
            return
        self._request = request
        self._pending_adds.clear()
        self._poll_signature = None
        self._root_media = None
        self._albums = None
        self._pending_tree_paths.clear()
        if self._batch_timer is not None:
            self._batch_timer.stop()
        if self._poll_timer is not None:
            self._poll_timer.stop()
        watcher = self._watcher
        if watcher is None:
            return
        self._apply_watch_paths(request)
        if self._poll_timer is not None:
            self._poll_timer.start()
        # A helper snapshot may be intentionally truncated to keep startup
        # bounded. Complete it once in this worker before relying on events.
        self._emit_snapshot(())

    @Slot(int)
    def cancel(self, generation: int) -> None:
        request = self._request
        if request is None or request.generation != generation:
            return
        self._request = None
        self._pending_adds.clear()
        if self._batch_timer is not None:
            self._batch_timer.stop()
        if self._poll_timer is not None:
            self._poll_timer.stop()
        watcher = self._watcher
        if watcher is not None and watcher.directories():
            watcher.removePaths(watcher.directories())

    @Slot()
    def shutdown(self) -> None:
        request = self._request
        if request is not None:
            self.cancel(request.generation)

    @Slot()
    def _add_next_batch(self) -> None:
        request = self._request
        watcher = self._watcher
        if request is None or watcher is None or request.polling:
            return
        batch = self._pending_adds[:32]
        del self._pending_adds[:32]
        if batch:
            watcher.addPaths(batch)
        if self._pending_adds and self._batch_timer is not None:
            self._batch_timer.start(0)

    @Slot(str)
    def _on_directory_changed(self, changed_path: str) -> None:
        request = self._request
        if request is None:
            return
        path = Path(changed_path)
        if path != request.root:
            # Qt may report a canonically equivalent Unicode spelling on macOS.
            try:
                if path.samefile(request.root):
                    path = request.root
            except OSError:
                pass
        self._emit_snapshot((path,), check_root_media=path == request.root)

    @Slot()
    def _poll(self) -> None:
        request = self._request
        if request is None:
            return
        changed_paths = ()
        if request.polling:
            signature = self._signature(request.paths)
            changed_paths = self._changed_signature_paths(self._poll_signature or (), signature)
            self._poll_signature = signature
        # Native directory notifications can miss in-place writes to root files.
        self._emit_snapshot(changed_paths)

    @Slot()
    def refresh(self) -> None:
        request = self._request
        if request is not None:
            self._emit_snapshot((request.root,))

    def _emit_snapshot(
        self, changed_paths: tuple[Path, ...], *, check_root_media: bool = True
    ) -> None:
        request = self._request
        if request is None:
            return
        root_media_changed = False
        self._pending_tree_paths.update(changed_paths)
        changed_paths = tuple(sorted(self._pending_tree_paths))
        try:
            entries = list(request.root.iterdir())
            previous_media = self._root_media
            if previous_media is None or check_root_media:
                include, exclude = (
                    request.scan_filters(request.root)
                    if request.scan_filters is not None
                    else (DEFAULT_INCLUDE, DEFAULT_EXCLUDE)
                )
                root_media = _root_media_signature(
                    request.root,
                    entries,
                    previous_media or {},
                    include or DEFAULT_INCLUDE,
                    exclude or DEFAULT_EXCLUDE,
                )
                root_media_changed = previous_media is not None and root_media != previous_media
                self._root_media = root_media
            if changed_paths or self._albums is None:
                entries.sort(key=lambda item: item.name.casefold())
                self._albums = _tree_snapshot(entries)
                self._pending_tree_paths.clear()
            elif not root_media_changed:
                return
            albums = self._albums
            warning = None
        except (OSError, IPhotoError) as exc:
            albums = ()
            warning = str(exc)
        current = self._request
        if current is None or current.generation != request.generation:
            return
        self.resultReady.emit(
            LibraryWatchResult(
                request.generation, changed_paths, albums, warning, root_media_changed
            )
        )
        desired = (request.root, *(node.path for node in albums))
        if warning is None and desired != request.paths:
            updated = replace(request, paths=desired)
            self._request = updated
            self._apply_watch_paths(updated)

    def _apply_watch_paths(self, request: LibraryWatchRequest) -> None:
        watcher = self._watcher
        if watcher is None:
            return
        existing = watcher.directories()
        if existing:
            watcher.removePaths(existing)
        self._pending_adds.clear()
        if request.polling:
            self._poll_signature = self._signature(request.paths)
            return
        self._pending_adds = [str(path) for path in request.paths]
        self._add_next_batch()

    @staticmethod
    def _signature(paths: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
        entries: list[tuple[str, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(entries))

    @staticmethod
    def _changed_signature_paths(
        previous: tuple[tuple[str, int, int], ...],
        current: tuple[tuple[str, int, int], ...],
    ) -> tuple[Path, ...]:
        previous_by_path = {path: values for path, *values in previous}
        current_by_path = {path: values for path, *values in current}
        return tuple(
            Path(path)
            for path in sorted(previous_by_path.keys() | current_by_path.keys())
            if previous_by_path.get(path) != current_by_path.get(path)
        )


class LibraryWatchService(QObject):
    """Own watcher I/O in a dedicated thread and reject stale generations."""

    resultReady = Signal(object)
    _configureRequested = Signal(object)
    _refreshRequested = Signal()
    _cancelRequested = Signal(int)
    _shutdownRequested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._configured_paths: tuple[Path, ...] = ()
        self._closed = False
        self._thread = QThread()
        self._thread.setObjectName("LibraryWatchService")
        self._worker = _LibraryWatchWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.initialize)
        self._thread.finished.connect(self._worker.deleteLater)
        self._configureRequested.connect(self._worker.configure)
        self._refreshRequested.connect(self._worker.refresh)
        self._cancelRequested.connect(self._worker.cancel)
        self._shutdownRequested.connect(self._worker.shutdown)
        self._worker.resultReady.connect(self._forward_result)
        application = QCoreApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.shutdown)

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def configured_paths(self) -> tuple[Path, ...]:
        return self._configured_paths

    def configure(
        self,
        root: Path,
        paths: tuple[Path, ...],
        *,
        polling: bool,
        scan_filters: Callable[[Path], tuple[list[str], list[str]]] | None = None,
    ) -> int:
        if self._closed:
            return self._generation
        if not self._thread.isRunning():
            self._thread.start()
        previous = self._generation
        if previous:
            self._cancelRequested.emit(previous)
        self._generation += 1
        self._configured_paths = paths
        self._configureRequested.emit(
            LibraryWatchRequest(self._generation, root, paths, polling, scan_filters)
        )
        return self._generation

    def cancel(self) -> None:
        if self._generation:
            self._cancelRequested.emit(self._generation)
        self._generation += 1
        self._configured_paths = ()

    def refresh(self) -> None:
        """Request fresh relocation hints without doing storage I/O in the caller."""

        if not self._closed:
            self._refreshRequested.emit()

    def shutdown(self) -> None:
        self._closed = True
        if not self._thread.isRunning():
            return
        self.cancel()
        self._shutdownRequested.emit()
        self._thread.quit()
        if not self._thread.wait(3000):
            self._thread.requestInterruption()
            self._thread.quit()
            self._thread.wait(2000)

    @Slot(object)
    def _forward_result(self, result: object) -> None:
        if not isinstance(result, LibraryWatchResult):
            return
        if result.generation != self._generation:
            return
        self.resultReady.emit(result)


__all__ = [
    "LibraryWatchRequest",
    "LibraryWatchResult",
    "LibraryWatchService",
]
