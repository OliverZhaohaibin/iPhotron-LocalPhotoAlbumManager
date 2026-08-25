"""Basic library runtime control: scanning, watching and editing albums.

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

from PySide6.QtCore import (
    QFileSystemWatcher,
    QMutex,
    QObject,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)

from ..errors import LibraryUnavailableError
from ..utils.logging import get_logger

# Mixin classes providing the extracted functionality
from .album_operations import AlbumOperationsMixin
from .filesystem_watcher import FileSystemWatcherMixin

# Re-export GeotaggedAsset for presentation widgets that need the map DTO.
from .geo_aggregator import (
    GeoAggregatorMixin,
    GeotaggedAsset,  # noqa: F401
)
from .scan_coordinator import ScanCoordinatorMixin
from .trash_manager import TrashManagerMixin
from .tree import AlbumNode
from .watch_service import LibraryWatchResult, LibraryWatchService

LOGGER = get_logger()
_STARTUP_RECOGNITION_IDLE_MS = 1500

if TYPE_CHECKING:  # pragma: no cover
    from ..application.ports import (
        AssetStateServicePort,
        EditServicePort,
        LibraryStateRepositoryPort,
        LocationAssetServicePort,
        MapInteractionServicePort,
        MapRuntimePort,
    )
    from ..bootstrap.library_album_metadata_service import LibraryAlbumMetadataService
    from ..bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService
    from ..bootstrap.library_asset_operation_service import LibraryAssetOperationService
    from ..bootstrap.library_asset_query_service import LibraryAssetQueryService
    from ..bootstrap.library_probe import PreparedLibrary
    from ..bootstrap.library_scan_service import LibraryScanService
    from ..bootstrap.library_session import LibrarySession
    from ..people.index_coordinator import PeopleIndexCoordinator
    from ..people.service import PeopleService
    from ..pets.index_coordinator import PetIndexCoordinator
    from ..pets.service import PetService
    from .workers.face_scan_worker import FaceScanWorker
    from .workers.pet_scan_worker import PetScanWorker
    from .workers.scanner_worker import ScannerWorker


class LibraryRuntimeController(
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
    scanBatchCommitted = Signal(object)
    scanFinished = Signal(Path, bool)
    scanBatchFailed = Signal(Path, int)
    peopleIndexUpdated = Signal()
    peopleSnapshotCommitted = Signal(object)
    faceScanStatusChanged = Signal(str)
    petIndexUpdated = Signal()
    petSnapshotCommitted = Signal(object)
    petScanStatusChanged = Signal(str)

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
        self._watch_service = LibraryWatchService(self)
        self._watch_service.resultReady.connect(self._on_background_watch_result)
        self._background_watch_generation = 0

        # Scanner State
        self._current_scanner_worker: Optional[ScannerWorker] = None
        self._current_face_scanner: Optional[FaceScanWorker] = None
        self._current_pet_scanner: Optional[PetScanWorker] = None
        self._cancelled_scanner_workers: set[int] = set()
        self._scan_thread_pool = QThreadPool(self)
        self._scan_thread_pool.setMaxThreadCount(1)
        self._scan_thread_pool.setThreadPriority(QThread.Priority.LowPriority)
        self._live_scan_buffer: List[Dict] = []
        self._live_scan_root: Optional[Path] = None
        self._deferred_scan_queue: list[tuple[Path, list[str], list[str], bool]] = []
        self._scan_buffer_lock = QMutex()
        self._geotagged_assets_cache: Optional[List[GeotaggedAsset]] = None
        self._geotagged_assets_cache_root: Optional[Path] = None
        self._face_scan_status_message: Optional[str] = None
        self._pet_scan_status_message: Optional[str] = None
        self._people_index_coordinator: PeopleIndexCoordinator | None = None
        self._pet_index_coordinator: PetIndexCoordinator | None = None
        self._recognition_services_root: Path | None = None
        self._recognition_scans_root: Path | None = None
        self._recognition_generation = 0
        self._startup_recognition_request: tuple[Path, int] | None = None
        self._startup_recognition_timer = QTimer(self)
        self._startup_recognition_timer.setSingleShot(True)
        self._startup_recognition_timer.setInterval(_STARTUP_RECOGNITION_IDLE_MS)
        self._startup_recognition_timer.timeout.connect(
            self._activate_startup_recognition_after_idle
        )
        self._delivered_recognition_event_ids: set[str] = set()
        self._retiring_recognition_workers: set[QThread] = set()
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
        self._pet_service: PetService | None = None
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

    def bind_prepared_library(self, prepared: "PreparedLibrary") -> None:
        """Publish a helper-produced tree without probing storage on the GUI thread."""

        self._clear_watches_for_rebind()
        self.stop_scanning()
        self._recognition_services_root = None
        self._recognition_scans_root = None
        self._delivered_recognition_event_ids.clear()
        self._pending_watch_paths.clear()
        self._watch_scan_queue.clear()
        self._root = Path(prepared.root)
        self._deleted_dir = None
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None

        albums: list[AlbumNode] = []
        children: Dict[Path, list[AlbumNode]] = {}
        nodes: Dict[Path, AlbumNode] = {}
        for item in prepared.albums:
            path = Path(item.path)
            node = AlbumNode(path, int(item.level), str(item.title), bool(item.has_manifest))
            nodes[path] = node
            if node.level == 1:
                albums.append(node)
                children.setdefault(path, [])
            elif node.level == 2:
                children.setdefault(path.parent, []).append(node)
        self._albums = sorted(albums, key=lambda item: item.title.casefold())
        self._children = {
            parent: sorted(items, key=lambda item: item.title.casefold())
            for parent, items in children.items()
        }
        self._nodes = nodes
        storage_profile = getattr(prepared, "storage_profile", None)
        storage_kind = getattr(storage_profile, "kind", prepared.storage_kind)
        polling = storage_kind in {"network", "removable"} or prepared.storage_kind == "slow"
        watch_paths = (self._root, *(node.path for node in self._nodes.values()))
        self._background_watch_generation = self._watch_service.configure(
            self._root,
            watch_paths,
            polling=polling,
        )
        LOGGER.info(
            "bind_prepared_library: root=%s albums=%d storage=%s",
            self._root,
            len(self._albums),
            prepared.storage_kind,
        )
        self.treeUpdated.emit()

    def _bind_path(self, root: Path, *, bind_session_if_needed: bool) -> None:
        LOGGER.info("bind_path: binding to %s", root)
        # Clear existing watches to ensure initialization operations (like creating
        # the deleted items folder) do not trigger "directoryChanged" signals
        # from an active watcher, which would cause a double-refresh.
        self._clear_watches_for_rebind()

        # Cancel any in-flight scan so we do not block UI interactions while
        # rebinding to a new library root.
        self.stop_scanning()
        self._recognition_services_root = None
        self._recognition_scans_root = None
        self._pending_watch_paths.clear()
        self._watch_scan_queue.clear()
        self._face_scan_status_message = None
        self._pet_scan_status_message = None
        self._unbind_people_index_coordinator()
        self._unbind_pet_index_coordinator()

        normalized = root.expanduser().resolve()
        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")
        self._root = normalized
        if bind_session_if_needed:
            self._bind_headless_session_if_needed(normalized)
        else:
            session = self._library_session
            if session is not None:
                try:
                    session_root = Path(session.library_root).resolve()
                except OSError:
                    session_root = Path(session.library_root)
                if session_root == normalized:
                    self.bind_recognition_services(session.people, session.pets)
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
            LOGGER.info("bind_path: watcher has no directories after refresh; rebuilding watches")
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
        self._watch_service.cancel()
        self._background_watch_generation = 0
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

        had_scanner_worker = self._current_scanner_worker is not None
        self._cancel_startup_recognition_request()
        self.stop_scanning(wait=True)
        self._recognition_services_root = None
        self._recognition_scans_root = None
        self._debounce.stop()
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._live_scan_buffer.clear()
        self._live_scan_root = None
        self._pending_watch_paths.clear()
        self._watch_scan_queue.clear()
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        self._unbind_people_index_coordinator()
        self._unbind_pet_index_coordinator()
        self._watch_service.shutdown()
        self._scan_thread_pool.clear()
        if not had_scanner_worker:
            self._scan_thread_pool.waitForDone(2000)

    def _on_background_watch_result(self, result: object) -> None:
        """Apply an immutable worker snapshot without traversing storage in GUI."""

        if not isinstance(result, LibraryWatchResult):
            return
        if result.generation != self._background_watch_generation or self._root is None:
            return
        if result.warning:
            LOGGER.warning("Library watcher refresh failed: %s", result.warning)
            return
        previous_paths = set(self._nodes)
        previous_nodes = dict(self._nodes)
        albums = [node for node in result.albums if node.level == 1]
        children: Dict[Path, list[AlbumNode]] = {node.path: [] for node in albums}
        nodes = {node.path: node for node in result.albums}
        for node in result.albums:
            if node.level == 2:
                children.setdefault(node.path.parent, []).append(node)
        self._albums = sorted(albums, key=lambda item: item.title.casefold())
        self._children = {
            parent: sorted(items, key=lambda item: item.title.casefold())
            for parent, items in children.items()
        }
        self._nodes = nodes
        tree_changed = nodes != previous_nodes
        if tree_changed:
            self._geotagged_assets_cache = None
            self._geotagged_assets_cache_root = None
            self.treeUpdated.emit()
        # Root watcher events include internal links/index maintenance.  Never
        # turn an unchanged root snapshot into a self-sustaining rescan loop;
        # new albums are still scanned, while direct-root changes remain an
        # explicit/manual refresh scope.
        changed = {path for path in result.changed_paths if path != self._root and path in nodes}
        changed.update(path for path in nodes if path not in previous_paths)
        self._start_watcher_scans(changed)

    def face_scan_status_message(self) -> str | None:
        return self._face_scan_status_message

    def pet_scan_status_message(self) -> str | None:
        return self._pet_scan_status_message

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
            self.bind_pet_service(None)
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
            self.bind_scan_service(library_session.scans)
            self.bind_asset_lifecycle_service(library_session.asset_lifecycle)
            self.bind_asset_operation_service(library_session.asset_operations)

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
        if (
            active_session is None
            and self._root is not None
            and asset_query_service is not None
            and self._location_service is None
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

    def bind_pet_service(self, pet_service: "PetService | None") -> None:
        """Bind the current library session Pets surface."""

        self._unbind_pet_index_coordinator()
        self._pet_service = pet_service
        if pet_service is None:
            return
        coordinator = pet_service.coordinator
        if coordinator is None:
            return
        coordinator.resume()
        coordinator.snapshotCommitted.connect(
            self._on_pet_snapshot_committed, Qt.ConnectionType.QueuedConnection
        )
        self._pet_index_coordinator = coordinator

    @property
    def pet_service(self) -> "PetService | None":
        return self._pet_service

    def bind_recognition_services(
        self,
        people_service: PeopleService | None,
        pet_service: "PetService | None",
    ) -> None:
        """Bind People/Pets without starting model workers."""

        # Read-only dashboard/overlay use must not import or construct the AI
        # coordinators. They are attached only when scanning is activated.
        self._people_service = people_service
        self._pet_service = pet_service
        root = self._root
        self._recognition_services_root = root
        if root != self._recognition_scans_root:
            self._recognition_scans_root = None
        if root is not None and pet_service is not None:
            from ..pets.pipeline import PET_DETECTOR_PIPELINE_VERSION

            repository = pet_service.repository()
            store = pet_service.asset_repository
            if repository is not None and store is not None:
                required_value = repository.get_scan_metadata("pet_backfill_required")
                previous_version = repository.get_scan_metadata("detector_pipeline_version")
                migration_target = repository.get_scan_metadata("detector_migration_target")
                migration_state = repository.get_scan_metadata("detector_migration_state")
                if not isinstance(required_value, (str, type(None))) or not isinstance(
                    previous_version,
                    (str, type(None)),
                ):
                    return
                migration_incomplete = (
                    migration_target == PET_DETECTOR_PIPELINE_VERSION
                    and migration_state in {"pending", "running"}
                )
                counts = store.count_by_pet_status()
                if not isinstance(counts, dict):
                    return
                ordinary_drain_required = (
                    int(counts.get("pending", 0)) > 0 or int(counts.get("retry", 0)) > 0
                )
                backfill_required = (
                    required_value == "1" or migration_incomplete or ordinary_drain_required
                )
                if required_value == "1" and migration_state not in {"pending", "running"}:
                    set_many = getattr(repository, "set_scan_metadata_many", None)
                    metadata = {
                        "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
                        "detector_migration_state": (
                            "running"
                            if previous_version == PET_DETECTOR_PIPELINE_VERSION
                            else "pending"
                        ),
                    }
                    if callable(set_many):
                        set_many(metadata)
                    else:
                        for key, value in metadata.items():
                            repository.set_scan_metadata(key, value)
                if previous_version != PET_DETECTOR_PIPELINE_VERSION:
                    backfill_required = backfill_required or int(counts.get("done", 0)) > 0
                    if backfill_required:
                        set_many = getattr(repository, "set_scan_metadata_many", None)
                        metadata = {
                            "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
                            "detector_migration_state": "pending",
                            "pet_backfill_required": "1",
                        }
                        if callable(set_many):
                            set_many(metadata)
                        else:
                            for key, value in metadata.items():
                                repository.set_scan_metadata(key, value)
                if backfill_required:
                    QTimer.singleShot(0, lambda: self._start_pet_backfill_worker(root))

    def activate_recognition_scans(self) -> None:
        """Start model workers after a recognition viewport is usable."""

        root = self._root
        if (
            root is None
            or root != self._recognition_services_root
            or root == self._recognition_scans_root
            or self._people_service is None
            or self._pet_service is None
        ):
            return
        self.bind_people_service(self._people_service)
        self.bind_pet_service(self._pet_service)
        if self._start_ai_scan_workers(root, startup=True):
            self._recognition_scans_root = root

    def request_startup_recognition_after_idle(self) -> None:
        """Start People/Pets after startup metadata is complete and input is idle."""

        root = self._root
        if root is None:
            return
        generation = int(self._recognition_generation)
        self._startup_recognition_request = (Path(root), generation)
        scanner = self._current_scanner_worker
        if scanner is not None and getattr(
            scanner, "_defer_ai_workers_until_scan_finished", False
        ):
            return
        self._arm_startup_recognition_idle_timer(Path(root), generation)

    def notify_user_activity(self) -> None:
        """Postpone a pending startup recognition scan without consuming input."""

        request = self._startup_recognition_request
        if request is None or not self._startup_recognition_timer.isActive():
            return
        self._startup_recognition_timer.start(_STARTUP_RECOGNITION_IDLE_MS)

    def _arm_startup_recognition_idle_timer(self, root: Path, generation: int) -> None:
        request = self._startup_recognition_request
        if (
            request != (Path(root), int(generation))
            or self._root != Path(root)
            or int(self._recognition_generation) != int(generation)
        ):
            return
        self._startup_recognition_timer.start(_STARTUP_RECOGNITION_IDLE_MS)

    def _cancel_startup_recognition_request(self) -> None:
        self._startup_recognition_timer.stop()
        self._startup_recognition_request = None

    def _activate_startup_recognition_after_idle(self) -> None:
        request = self._startup_recognition_request
        if request is None:
            return
        root, generation = request
        self._startup_recognition_request = None
        if (
            self._root != root
            or int(self._recognition_generation) != generation
            or self._current_scanner_worker is not None
        ):
            return
        session = self._library_session
        if session is None or Path(session.library_root) != root:
            return
        self.bind_recognition_services(session.people, session.pets)
        self.activate_recognition_scans()

    def activate_recognition_services(
        self,
        people_service: PeopleService | None,
        pet_service: "PetService | None",
    ) -> None:
        """Compatibility wrapper for non-GUI callers requiring eager scans."""

        self.bind_recognition_services(people_service, pet_service)
        self.activate_recognition_scans()

    def activate_map_services(
        self,
        location_service: "LocationAssetServicePort | None",
        map_runtime: "MapRuntimePort | None",
        map_interaction_service: "MapInteractionServicePort | None",
    ) -> None:
        """Bind Location and Maps services together on first map use."""

        self.bind_location_service(location_service)
        self.bind_map_runtime(map_runtime)
        self.bind_map_interaction_service(map_interaction_service)

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
        from ..people.index_coordinator import get_people_index_coordinator

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
            self.bind_pet_service(session.pets)
            self.bind_location_service(session.locations)
            self.bind_edit_service(session.edit)
            self.bind_map_runtime(session.maps)
            self.bind_map_interaction_service(session.map_interactions)
            return

        from ..bootstrap.library_session import create_headless_library_session

        session = create_headless_library_session(root)
        self.bind_library_session(session, owned=True)
        # Explicit synchronous/headless entry points keep the complete legacy
        # service surface.  Prepared GUI startup stays Gallery-only until use.
        self.bind_people_service(session.people)
        self.bind_pet_service(session.pets)
        self.bind_location_service(session.locations)
        self.bind_edit_service(session.edit)
        self.bind_map_runtime(session.maps)
        self.bind_map_interaction_service(session.map_interactions)

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

    def _unbind_pet_index_coordinator(self) -> None:
        if self._pet_index_coordinator is None:
            return
        self._pet_index_coordinator.begin_shutdown()
        try:
            self._pet_index_coordinator.snapshotCommitted.disconnect(
                self._on_pet_snapshot_committed
            )
        except (RuntimeError, TypeError):
            pass
        self._pet_index_coordinator = None

    def _on_people_snapshot_committed(self, event: object) -> None:
        if self._recognition_event_was_delivered(event):
            return
        pet_service = self._pet_service
        if pet_service is not None:
            try:
                pet_service.reconcile_people_overlaps(getattr(event, "changed_asset_ids", ()))
            except Exception:  # noqa: BLE001 - do not break People snapshot delivery
                LOGGER.warning(
                    "Failed to reconcile People-priority pet detections for %s",
                    self._root,
                    exc_info=True,
                )
        self.peopleIndexUpdated.emit()
        self.peopleSnapshotCommitted.emit(event)

    def _on_pet_snapshot_committed(self, event: object) -> None:
        if self._recognition_event_was_delivered(event):
            return
        self.petIndexUpdated.emit()
        self.petSnapshotCommitted.emit(event)

    def _recognition_event_was_delivered(self, event: object) -> bool:
        event_id = str(getattr(event, "event_id", None) or "")
        if not event_id:
            return False
        delivered = getattr(self, "_delivered_recognition_event_ids", None)
        if delivered is None:
            delivered = set()
            self._delivered_recognition_event_ids = delivered
        if event_id in delivered:
            return True
        delivered.add(event_id)
        if len(delivered) > 4096:
            delivered.pop()
        return False

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
            child_nodes = [
                self._build_node(child, level=2) for child in self._iter_album_dirs(album_dir)
            ]
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


__all__ = ["GeotaggedAsset", "LibraryRuntimeController"]
