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
from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, Signal, QThreadPool, QMutex

from ..errors import LibraryUnavailableError
from ..people.index_coordinator import PeopleIndexCoordinator, get_people_index_coordinator
from ..people.service import PeopleService
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

if TYPE_CHECKING:  # pragma: no cover
    from ..application.ports import (
        AssetStateServicePort,
        EditServicePort,
        LocationAssetServicePort,
        MapInteractionServicePort,
        MapRuntimePort,
    )
    from ..bootstrap.library_album_metadata_service import LibraryAlbumMetadataService
    from ..bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService
    from ..bootstrap.library_asset_operation_service import LibraryAssetOperationService
    from ..bootstrap.library_asset_query_service import LibraryAssetQueryService
    from ..bootstrap.library_session import LibrarySession
    from ..bootstrap.library_scan_service import LibraryScanService
    from ..application.ports import LibraryStateRepositoryPort


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
        self._pending_watch_paths: set[Path] = set()
        self._watch_scan_queue: list[Path] = []
        # ``_watch_suspend_depth`` tracks how many in-flight operations asked us to
        # ignore file-system notifications. We use a counter instead of a boolean
        # to correctly handle nested operations that may overlap (e.g., multiple
        # concurrent file operations that each need to pause/resume the watcher).
        self._watch_suspend_depth = 0
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._debounce.timeout.connect(self._on_watcher_debounce_timeout)
        self.scanFinished.connect(self._on_watcher_scan_finished)

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
        self._library_session: "LibrarySession | None" = None
        self._owns_library_session = False
        self._scan_service: "LibraryScanService | None" = None
        self._asset_query_service: "LibraryAssetQueryService | None" = None
        self._state_repository: "LibraryStateRepositoryPort | None" = None
        self._asset_state_service: "AssetStateServicePort | None" = None
        self._album_metadata_service: "LibraryAlbumMetadataService | None" = None
        self._asset_lifecycle_service: "LibraryAssetLifecycleService | None" = None
        self._asset_operation_service: "LibraryAssetOperationService | None" = None
        self._people_service: PeopleService | None = None
        self._map_runtime: "MapRuntimePort | None" = None
        self._map_interaction_service: "MapInteractionServicePort | None" = None
        self._edit_service: "EditServicePort | None" = None
        self._location_service: "LocationAssetServicePort | None" = None

    # ------------------------------------------------------------------
    # Basic properties
    # ------------------------------------------------------------------
    def root(self) -> Path | None:
        return self._root

    def invalidate_geotagged_assets_cache(self, *, emit_tree_updated: bool = False) -> None:
        """Drop cached map assets and optionally notify the UI to refresh views."""

        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        location_service = getattr(self, "location_service", None)
        invalidate_cache = getattr(location_service, "invalidate_cache", None)
        if callable(invalidate_cache):
            invalidate_cache()
        if emit_tree_updated:
            self.treeUpdated.emit()

    # ------------------------------------------------------------------
    # Binding and tree coordination
    # ------------------------------------------------------------------
    def bind_path(self, root: Path) -> None:
        self._bind_path(root, bind_session_if_needed=True)

    def bind_path_from_session(self, root: Path) -> None:
        """Bind *root* without creating a headless compatibility session."""

        self._bind_path(root, bind_session_if_needed=False)

    def _bind_path(self, root: Path, *, bind_session_if_needed: bool) -> None:
        LOGGER.info("bind_path: binding to %s", root)
        # Clear existing watches to ensure initialization operations (like creating
        # the deleted items folder) do not trigger "directoryChanged" signals
        # from an active watcher, which would cause a double-refresh.
        self._clear_watches_for_rebind()

        # Cancel any in-flight scan so we do not block UI interactions while
        # rebinding to a new library root.
        self.stop_scanning()
        self._pending_watch_paths.clear()
        self._watch_scan_queue.clear()
        self._face_scan_status_message = None
        self._unbind_people_index_coordinator()

        normalized = root.expanduser().resolve()
        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")
        self._root = normalized
        if bind_session_if_needed:
            self._bind_headless_session_if_needed(normalized)
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

    def _clear_watches_for_rebind(self) -> None:
        existing_dirs = self._watcher.directories()
        existing_files = self._watcher.files()
        if existing_dirs:
            self._watcher.removePaths(existing_dirs)
        if existing_files:
            self._watcher.removePaths(existing_files)
        if not self._watcher.directories() and not self._watcher.files():
            return

        try:
            self._watcher.directoryChanged.disconnect(self._on_directory_changed)
        except (RuntimeError, TypeError):
            pass
        self._watcher.deleteLater()
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)

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
        self._pending_watch_paths.clear()
        self._watch_scan_queue.clear()
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

    def bind_library_session(
        self,
        library_session: "LibrarySession | None",
        *,
        owned: bool = False,
    ) -> None:
        """Bind or clear the active library session surface for this manager."""

        previous = self._library_session
        previous_owned = self._owns_library_session
        if previous is library_session and previous is not None:
            self._owns_library_session = owned
            return

        self._library_session = library_session
        self._owns_library_session = bool(library_session is not None and owned)

        if library_session is None:
            self.bind_location_service(None)
            self.bind_edit_service(None)
            self.bind_map_interaction_service(None)
            self.bind_map_runtime(None)
            self.bind_people_service(None)
            self.bind_asset_operation_service(None)
            self.bind_asset_lifecycle_service(None)
            self.bind_album_metadata_service(None)
            self.bind_asset_state_service(None)
            self.bind_state_repository(None)
            self.bind_asset_query_service(None)
            self.bind_scan_service(None)
        else:
            self.bind_asset_query_service(library_session.asset_queries)
            self.bind_state_repository(library_session.state)
            self.bind_asset_state_service(library_session.asset_state)
            self.bind_album_metadata_service(library_session.album_metadata)
            self.bind_location_service(library_session.locations)
            self.bind_edit_service(library_session.edit)
            self.bind_scan_service(library_session.scans)
            self.bind_asset_lifecycle_service(library_session.asset_lifecycle)
            self.bind_asset_operation_service(library_session.asset_operations)
            self.bind_people_service(library_session.people)
            self.bind_map_runtime(library_session.maps)
            self.bind_map_interaction_service(library_session.map_interactions)

        if previous is not None and previous is not library_session and previous_owned:
            previous.shutdown()

    @property
    def library_session(self) -> "LibrarySession | None":
        return self._library_session

    def bind_scan_service(self, scan_service: "LibraryScanService | None") -> None:
        """Bind the current library session scan command surface."""

        self._scan_service = scan_service

    @property
    def scan_service(self) -> "LibraryScanService | None":
        return self._scan_service

    def bind_asset_query_service(
        self,
        asset_query_service: "LibraryAssetQueryService | None",
    ) -> None:
        """Bind the current library session asset query surface."""

        self._asset_query_service = asset_query_service
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        active_session = self._library_session
        default_location_service = (
            active_session.locations if active_session is not None else None
        )
        if (
            self._root is not None
            and asset_query_service is not None
            and self._location_service is default_location_service
        ):
            from ..bootstrap.library_location_service import LibraryLocationService

            self._location_service = LibraryLocationService(
                self._root,
                query_service=asset_query_service,
            )

    @property
    def asset_query_service(self) -> "LibraryAssetQueryService | None":
        return self._asset_query_service

    def bind_state_repository(
        self,
        state_repository: "LibraryStateRepositoryPort | None",
    ) -> None:
        """Bind the current library session durable-state surface."""

        self._state_repository = state_repository

    @property
    def state_repository(self) -> "LibraryStateRepositoryPort | None":
        return self._state_repository

    def bind_asset_state_service(
        self,
        asset_state_service: "AssetStateServicePort | None",
    ) -> None:
        """Bind the current library session asset-state command surface."""

        self._asset_state_service = asset_state_service

    @property
    def asset_state_service(self) -> "AssetStateServicePort | None":
        return self._asset_state_service

    def bind_album_metadata_service(
        self,
        album_metadata_service: "LibraryAlbumMetadataService | None",
    ) -> None:
        """Bind the current library session album metadata command surface."""

        self._album_metadata_service = album_metadata_service

    @property
    def album_metadata_service(self) -> "LibraryAlbumMetadataService | None":
        return self._album_metadata_service

    def bind_asset_lifecycle_service(
        self,
        asset_lifecycle_service: "LibraryAssetLifecycleService | None",
    ) -> None:
        """Bind the current library session asset lifecycle command surface."""

        self._asset_lifecycle_service = asset_lifecycle_service

    @property
    def asset_lifecycle_service(self) -> "LibraryAssetLifecycleService | None":
        return self._asset_lifecycle_service

    def bind_asset_operation_service(
        self,
        asset_operation_service: "LibraryAssetOperationService | None",
    ) -> None:
        """Bind the current library session file-operation command surface."""

        self._asset_operation_service = asset_operation_service

    @property
    def asset_operation_service(self) -> "LibraryAssetOperationService | None":
        return self._asset_operation_service

    def bind_people_service(self, people_service: PeopleService | None) -> None:
        """Bind the current library session People surface."""

        self._unbind_people_index_coordinator()
        self._people_service = people_service
        if people_service is None:
            return
        coordinator = people_service.coordinator
        if coordinator is None:
            return
        coordinator.resume()
        coordinator.snapshotCommitted.connect(
            self._on_people_snapshot_committed, Qt.ConnectionType.QueuedConnection
        )
        self._people_index_coordinator = coordinator

    @property
    def people_service(self) -> PeopleService | None:
        return self._people_service

    def bind_map_runtime(self, map_runtime: "MapRuntimePort | None") -> None:
        """Bind the current library session Maps runtime surface."""

        self._map_runtime = map_runtime

    @property
    def map_runtime(self) -> "MapRuntimePort | None":
        return self._map_runtime

    def bind_map_interaction_service(
        self,
        map_interaction_service: "MapInteractionServicePort | None",
    ) -> None:
        """Bind the current library session Maps interaction surface."""

        self._map_interaction_service = map_interaction_service

    @property
    def map_interaction_service(self) -> "MapInteractionServicePort | None":
        return self._map_interaction_service

    def bind_edit_service(self, edit_service: "EditServicePort | None") -> None:
        """Bind the current library session edit surface."""

        self._edit_service = edit_service

    @property
    def edit_service(self) -> "EditServicePort | None":
        return self._edit_service

    def bind_location_service(
        self,
        location_service: "LocationAssetServicePort | None",
    ) -> None:
        """Bind the current library session Location query surface."""

        self._location_service = location_service
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None

    @property
    def location_service(self) -> "LocationAssetServicePort | None":
        return self._location_service

    def _bind_people_index_coordinator(self, root: Path) -> None:
        coordinator = get_people_index_coordinator(root)
        coordinator.resume()
        coordinator.snapshotCommitted.connect(
            self._on_people_snapshot_committed, Qt.ConnectionType.QueuedConnection
        )
        self._people_index_coordinator = coordinator

    def _bind_headless_session_if_needed(self, root: Path) -> None:
        session = self._library_session
        if session is not None and session.library_root == root:
            # ``bind_path()`` temporarily disconnects the People coordinator while
            # clearing state for a rebind. When a GUI-owned session is already
            # bound for this root, restore the People surface so snapshot events
            # keep flowing after the tree refresh completes.
            self.bind_people_service(session.people)
            return

        from ..bootstrap.library_session import create_headless_library_session

        self.bind_library_session(
            create_headless_library_session(root),
            owned=True,
        )

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
