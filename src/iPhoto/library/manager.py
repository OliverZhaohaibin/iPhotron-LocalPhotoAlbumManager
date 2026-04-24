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

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, Signal, QThreadPool, QMutex

from ..errors import LibraryUnavailableError
from ..people.index_coordinator import PeopleIndexCoordinator, get_people_index_coordinator
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
from .workers.face_scan_worker import FaceScanWorker
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
    albumRenamed = Signal(Path, Path)
    errorRaised = Signal(str)

    # Scanner signals exposed for the facade
    scanProgress = Signal(Path, int, int)
    scanChunkReady = Signal(Path, list)
    scanFinished = Signal(Path, bool)
    scanBatchFailed = Signal(Path, int)
    peopleIndexUpdated = Signal()
    peopleSnapshotCommitted = Signal(object)
    faceScanStatusChanged = Signal(str)

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
        self._current_face_scanner: Optional[FaceScanWorker] = None
        self._scan_thread_pool = QThreadPool.globalInstance()
        self._live_scan_buffer: List[Dict] = []
        self._live_scan_root: Optional[Path] = None
        self._scan_buffer_lock = QMutex()
        self._geotagged_assets_cache: Optional[List[GeotaggedAsset]] = None
        self._geotagged_assets_cache_root: Optional[Path] = None
        self._face_scan_status_message: Optional[str] = None
        self._people_index_coordinator: PeopleIndexCoordinator | None = None

    # ------------------------------------------------------------------
    # Basic properties
    # ------------------------------------------------------------------
    def root(self) -> Path | None:
        return self._root

    def invalidate_geotagged_assets_cache(self, *, emit_tree_updated: bool = False) -> None:
        """Drop cached map assets and optionally notify the UI to refresh views."""

        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        if emit_tree_updated:
            self.treeUpdated.emit()

    # ------------------------------------------------------------------
    # Binding and tree coordination
    # ------------------------------------------------------------------
    def bind_path(self, root: Path) -> None:
        LOGGER.info("bind_path: binding to %s", root)
        # Clear existing watches to ensure initialization operations (like creating
        # the deleted items folder) do not trigger "directoryChanged" signals
        # from an active watcher, which would cause a double-refresh.
        if existing := self._watcher.directories():
            self._watcher.removePaths(existing)

        # Cancel any in-flight scan so we do not block UI interactions while
        # rebinding to a new library root.
        self.stop_scanning()
        self._face_scan_status_message = None
        self._unbind_people_index_coordinator()

        normalized = root.expanduser().resolve()
        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")
        self._root = normalized
        self._bind_people_index_coordinator(normalized)
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        LOGGER.info("bind_path: normalized root=%s", normalized)
        self._initialize_deleted_dir()
        self._refresh_tree()
        # If the album tree was unchanged, ``_refresh_tree()`` may have skipped
        # rebuilding the QFileSystemWatcher paths. Because ``bind_path()`` just
        # cleared all watcher directories, ensure we restore them so filesystem
        # monitoring is active even when binding an (initially) empty library.
        if not self._watcher.directories():
            LOGGER.info(
                "bind_path: watcher has no directories after refresh; rebuilding watches"
            )
            self._rebuild_watches()
        # ``_refresh_tree()`` skips the ``treeUpdated`` emission when the album
        # list is unchanged (an optimisation for filesystem-watcher refreshes).
        # When binding a library for the first time the album list may be empty
        # both before and after the call, yet the UI model still needs to
        # transition from the "Bind Basic Library…" placeholder to the full
        # tree.  Emitting here only when the album list is empty preserves that
        # initial-model-rebuild behaviour without causing duplicate emissions
        # for non-empty libraries where ``_refresh_tree()`` has already emitted.
        if not self._albums:
            LOGGER.info("bind_path: emitting treeUpdated for empty album tree")
            self.treeUpdated.emit()

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
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        if self._current_face_scanner is not None:
            self._current_face_scanner.cancel()
            self._current_face_scanner.wait(2000)
            if self._current_face_scanner.isRunning():
                LOGGER.warning(
                    "Face scan worker did not exit within 2 s after cancel(); "
                    "detaching without terminate() to avoid DB corruption."
                )
            self._current_face_scanner = None
        self._unbind_people_index_coordinator()

    def face_scan_status_message(self) -> str | None:
        return self._face_scan_status_message

    def _bind_people_index_coordinator(self, root: Path) -> None:
        coordinator = get_people_index_coordinator(root)
        coordinator.resume()
        coordinator.snapshotCommitted.connect(
            self._on_people_snapshot_committed, Qt.ConnectionType.QueuedConnection
        )
        self._people_index_coordinator = coordinator

    def _unbind_people_index_coordinator(self) -> None:
        if self._people_index_coordinator is None:
            return
        self._people_index_coordinator.begin_shutdown()
        try:
            self._people_index_coordinator.snapshotCommitted.disconnect(
                self._on_people_snapshot_committed
            )
        except (RuntimeError, TypeError):
            pass
        self._people_index_coordinator = None

    def _on_people_snapshot_committed(self, event: object) -> None:
        self.peopleIndexUpdated.emit()
        self.peopleSnapshotCommitted.emit(event)

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
            self._geotagged_assets_cache = None
            self._geotagged_assets_cache_root = None
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
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        self._rebuild_watches()
        self.treeUpdated.emit()


__all__ = ["GeotaggedAsset", "LibraryManager"]
