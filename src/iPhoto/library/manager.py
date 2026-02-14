"""Basic library management: scanning, watching and editing albums.

This module acts as a coordinator/facade. The heavy lifting is delegated to
sub-modules extracted during a refactoring pass:

* :mod:`.album_operations`   – Album CRUD and manifest helpers
* :mod:`.scan_coordinator`   – Background scan scheduling & progress
* :mod:`.filesystem_watcher` – ``QFileSystemWatcher`` wrapper
* :mod:`.geo_aggregator`     – ``GeotaggedAsset`` dataclass & collection
* :mod:`.trash_manager`      – Trash / deleted-items management
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal, QThreadPool, QMutex

from ..errors import LibraryUnavailableError
from ..utils.logging import get_logger
from .tree import AlbumNode

# Re-export GeotaggedAsset so ``from iPhoto.library.manager import GeotaggedAsset`` keeps working.
from .geo_aggregator import GeotaggedAsset  # noqa: F401

# Mixin classes providing the extracted functionality
from .album_operations import AlbumOperationsMixin
from .scan_coordinator import ScanCoordinatorMixin
from .filesystem_watcher import FileSystemWatcherMixin
from .geo_aggregator import GeoAggregatorMixin
from .trash_manager import TrashManagerMixin

# Workers are still needed for type annotations in __init__
from .workers.scanner_worker import ScannerWorker

LOGGER = get_logger()


class LibraryManager(
    AlbumOperationsMixin,
    ScanCoordinatorMixin,
    FileSystemWatcherMixin,
    GeoAggregatorMixin,
    TrashManagerMixin,
    QObject,
):
    """Manage the Basic Library tree, file-system helpers, and scanning state."""

    treeUpdated = Signal()
    errorRaised = Signal(str)

    # Scanner signals exposed for the facade
    scanProgress = Signal(Path, int, int)
    scanChunkReady = Signal(Path, list)
    scanFinished = Signal(Path, bool)
    scanBatchFailed = Signal(Path, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root: Path | None = None
        self._albums: list[AlbumNode] = []
        self._children: Dict[Path, list[AlbumNode]] = {}
        self._nodes: Dict[Path, AlbumNode] = {}
        self._deleted_dir: Path | None = None
        self._watcher = QFileSystemWatcher(self)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(500)
        # ``_watch_suspend_depth`` tracks how many in-flight operations asked us to
        # ignore file-system notifications. We use a counter instead of a boolean
        # to correctly handle nested operations that may overlap (e.g., multiple
        # concurrent file operations that each need to pause/resume the watcher).
        self._watch_suspend_depth = 0
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._debounce.timeout.connect(self._refresh_tree)

        # Scanner State
        self._current_scanner_worker: Optional[ScannerWorker] = None
        self._scan_thread_pool = QThreadPool.globalInstance()
        self._live_scan_buffer: List[Dict] = []
        self._live_scan_root: Optional[Path] = None
        self._scan_buffer_lock = QMutex()

    # ------------------------------------------------------------------
    # Basic properties
    # ------------------------------------------------------------------
    def root(self) -> Path | None:
        return self._root

    # ------------------------------------------------------------------
    # Binding and tree coordination
    # ------------------------------------------------------------------
    def bind_path(self, root: Path) -> None:
        # Clear existing watches to ensure initialization operations (like creating
        # the deleted items folder) do not trigger "directoryChanged" signals
        # from an active watcher, which would cause a double-refresh.
        if existing := self._watcher.directories():
            self._watcher.removePaths(existing)

        # Cancel any in-flight scan so we do not block UI interactions while
        # rebinding to a new library root.
        self.stop_scanning()

        normalized = root.expanduser().resolve()
        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")
        self._root = normalized
        self._initialize_deleted_dir()
        self._refresh_tree()

    def list_albums(self) -> list[AlbumNode]:
        return list(self._albums)

    def list_children(self, album: AlbumNode) -> list[AlbumNode]:
        return list(self._children.get(album.path, []))

    def scan_tree(self) -> list[AlbumNode]:
        self._refresh_tree()
        return self.list_albums()

    def shutdown(self) -> None:
        """Stop background workers and watchers during application shutdown."""

        self.stop_scanning()
        self._debounce.stop()
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        self._live_scan_buffer.clear()
        self._live_scan_root = None

    # ------------------------------------------------------------------
    # Internal helpers (coordinator-level)
    # ------------------------------------------------------------------
    def _require_root(self) -> Path:
        if self._root is None:
            raise LibraryUnavailableError("Basic Library path has not been configured.")
        return self._root

    def _refresh_tree(self) -> None:
        if self._root is None:
            self._albums = []
            self._children = {}
            self._nodes = {}
            self._deleted_dir = None
            self._rebuild_watches()
            self.treeUpdated.emit()
            return
        previous_albums = self._albums
        previous_children = self._children
        previous_nodes = self._nodes
        albums: list[AlbumNode] = []
        children: Dict[Path, list[AlbumNode]] = {}
        new_nodes: Dict[Path, AlbumNode] = {}
        for album_dir in self._iter_album_dirs(self._root):
            node = self._build_node(album_dir, level=1)
            albums.append(node)
            new_nodes[album_dir] = node
            child_nodes = [self._build_node(child, level=2) for child in self._iter_album_dirs(album_dir)]
            for child in child_nodes:
                new_nodes[child.path] = child
            children[album_dir] = child_nodes
        refreshed_albums = sorted(albums, key=lambda item: item.title.casefold())
        refreshed_children = {
            parent: sorted(kids, key=lambda item: item.title.casefold())
            for parent, kids in children.items()
        }
        if (
            new_nodes == previous_nodes
            and refreshed_albums == previous_albums
            and refreshed_children == previous_children
        ):
            return
        self._albums = refreshed_albums
        self._children = refreshed_children
        self._nodes = new_nodes
        self._rebuild_watches()
        self.treeUpdated.emit()


__all__ = ["GeotaggedAsset", "LibraryManager"]
