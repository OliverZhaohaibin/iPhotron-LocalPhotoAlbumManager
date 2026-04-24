"""Qt-aware facade that bridges the CLI backend to the GUI layer."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set, TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from .. import app as backend
from ..cache.index_store import get_global_repository
from ..config import DEFAULT_INCLUDE, DEFAULT_EXCLUDE
from ..errors import IPhotoError
from ..models.album import Album
from ..utils.logging import get_logger
from .background_task_manager import BackgroundTaskManager
from .services import (
    AlbumMetadataService,
    AssetImportService,
    AssetMoveService,
    DeletionService,
    LibraryUpdateService,
    RestorationService,
)

if TYPE_CHECKING:
    from ..library.manager import LibraryManager

import logging
logger = logging.getLogger(__name__)

class AppFacade(QObject):
    """Expose high-level album operations to the GUI layer."""

    albumOpened = Signal(Path)
    albumCoverUpdated = Signal(Path, Path)
    assetUpdated = Signal(Path)
    indexUpdated = Signal(Path)
    linksUpdated = Signal(Path)
    errorRaised = Signal(str)
    scanProgress = Signal(Path, int, int)
    scanChunkReady = Signal(Path, list)
    scanFinished = Signal(Path, bool)
    scanBatchFailed = Signal(Path, int)
    loadStarted = Signal(Path)
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)
    activeModelChanged = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._logger = get_logger()
        self._current_album: Optional[Album] = None
        self._pending_index_announcements: Set[Path] = set()
        self._library_manager: Optional["LibraryManager"] = None
        self._restore_prompt_handler: Optional[Callable[[str], bool]] = None
        self._model_provider: Optional[Callable[[], Any]] = None

        def _pause_watcher() -> None:
            """Suspend the library watcher while background tasks mutate files."""

            manager = self._library_manager
            if manager is not None:
                manager.pause_watcher()

        def _resume_watcher() -> None:
            """Resume filesystem monitoring after background work completes."""

            manager = self._library_manager
            if manager is not None:
                manager.resume_watcher()

        self._task_manager = BackgroundTaskManager(
            pause_watcher=_pause_watcher,
            resume_watcher=_resume_watcher,
            parent=self,
        )

        self._metadata_service = AlbumMetadataService(
            current_album_getter=lambda: self._current_album,
            library_manager_getter=self._get_library_manager,
            refresh_view=self._refresh_view,
            parent=self,
        )
        self._metadata_service.errorRaised.connect(self._on_service_error)

        self._library_update_service = LibraryUpdateService(
            task_manager=self._task_manager,
            current_album_getter=lambda: self._current_album,
            library_manager_getter=self._get_library_manager,
            parent=self,
        )

        self._library_update_service.scanProgress.connect(self._relay_scan_progress)
        self._library_update_service.scanChunkReady.connect(self._relay_scan_chunk_ready)
        self._library_update_service.scanFinished.connect(self._relay_scan_finished)
        self._library_update_service.indexUpdated.connect(self._relay_index_updated)
        self._library_update_service.linksUpdated.connect(self._relay_links_updated)
        self._library_update_service.assetReloadRequested.connect(
            self._on_asset_reload_requested
        )
        self._library_update_service.errorRaised.connect(self._on_service_error)

        self._import_service = AssetImportService(
            task_manager=self._task_manager,
            current_album_root=self._current_album_root,
            update_service=self._library_update_service,
            metadata_service=self._metadata_service,
            library_manager_getter=self._get_library_manager,
            parent=self,
        )
        self._import_service.errorRaised.connect(self._on_service_error)

        self._move_service = AssetMoveService(
            task_manager=self._task_manager,
            current_album_getter=lambda: self._current_album,
            library_manager_getter=self._get_library_manager,
            parent=self,
        )
        self._move_service.errorRaised.connect(self._on_service_error)
        self._move_service.moveCompletedDetailed.connect(
            self._library_update_service.handle_move_operation_completed
        )

        self._deletion_service = DeletionService(
            move_service=self._move_service,
            library_manager_getter=self._get_library_manager,
            model_provider_getter=lambda: self._model_provider,
            parent=self,
        )
        self._deletion_service.errorRaised.connect(self._on_service_error)

        self._restoration_service = RestorationService(
            move_service=self._move_service,
            library_manager_getter=self._get_library_manager,
            model_provider_getter=lambda: self._model_provider,
            restore_prompt_getter=lambda: self._restore_prompt_handler,
            parent=self,
        )
        self._restoration_service.errorRaised.connect(self._on_service_error)

    def set_model_provider(self, provider: Callable[[], Any]):
        """Inject the new ViewModel provider for legacy operations."""
        self._model_provider = provider

    # ------------------------------------------------------------------
    # Album lifecycle
    # ------------------------------------------------------------------
    @property
    def current_album(self) -> Optional[Album]:
        """Return the album currently loaded in the facade."""

        return self._current_album

    @property
    def import_service(self) -> AssetImportService:
        """Expose the import service so controllers can observe its signals."""

        return self._import_service

    @property
    def move_service(self) -> AssetMoveService:
        """Expose the move service so controllers can observe its signals."""

        return self._move_service

    @property
    def metadata_service(self) -> AlbumMetadataService:
        """Provide access to the manifest service for advanced controllers."""

        return self._metadata_service

    @property
    def library_updates(self) -> LibraryUpdateService:
        """Expose the library update service for direct signal subscriptions."""

        return self._library_update_service

    @property
    def library_manager(self) -> Optional["LibraryManager"]:
        """Expose the underlying library manager."""

        return self._library_manager

    def open_album(self, root: Path) -> Optional[Album]:
        """Open *root* and trigger background work as needed."""

        # Get library root first for global database access
        library_root = self._library_manager.root() if self._library_manager else None
        
        try:
            album = backend.open_album(
                root,
                autoscan=False,
                library_root=library_root,
                hydrate_index=False,
            )
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            return None

        self._current_album = album
        album_root = album.root

        self.albumOpened.emit(album_root)

        # Check if the index is empty (likely because it's a new or cleaned album)
        # and trigger a background scan if necessary.
        index_root = library_root if library_root else album_root
        has_assets = False
        try:
            store = get_global_repository(index_root)
            next(store.read_all())
            has_assets = True
        except (StopIteration, IPhotoError):
            pass

        is_already_scanning = False
        if self._library_manager and self._library_manager.is_scanning_path(album_root):
            is_already_scanning = True

        if not has_assets and not is_already_scanning:
            self.rescan_current_async()

        # Legacy reload signals - might be needed for status bar
        self.loadStarted.emit(album_root)
        self.loadFinished.emit(album_root, True)

        return album

    def rescan_current(self) -> List[dict]:
        """Rescan the active album and emit ``indexUpdated`` when done."""

        album = self._require_album()
        if album is None:
            return []
        return self._library_update_service.rescan_album(album)

    def rescan_current_async(self) -> None:
        """Start a background rescan for the active album."""

        album = self._require_album()
        if album is None:
            return

        if self._library_manager:
            filters = album.manifest.get("filters", {}) if isinstance(album.manifest, dict) else {}
            include = filters.get("include", DEFAULT_INCLUDE)
            exclude = filters.get("exclude", DEFAULT_EXCLUDE)

            self._library_manager.start_scanning(album.root, include, exclude)
        else:
            self._library_update_service.rescan_album_async(album)

    def _inject_scan_dependencies_for_tests(
        self,
        *,
        library_manager: Optional["LibraryManager"] = None,
        library_update_service: Optional[LibraryUpdateService] = None,
    ) -> None:
        """Override scan collaborators during testing."""

        if library_manager is not None:
            self._library_manager = library_manager
        if library_update_service is not None:
            self._library_update_service = library_update_service

    def cancel_active_scans(self) -> None:
        """Request cancellation of any in-flight scan operations."""

        if self._library_manager is not None:
            try:
                self._library_manager.stop_scanning()
                self._library_manager.pause_watcher()
            except RuntimeError:
                self._logger.warning("Failed to stop active scan during shutdown", exc_info=True)

        self._library_update_service.cancel_active_scan()

    def is_performing_background_operation(self) -> bool:
        """Return ``True`` while imports or moves are still running."""

        return self._task_manager.has_watcher_blocking_tasks()

    def pair_live_current(self) -> List[dict]:
        """Rebuild Live Photo pairings for the active album."""

        album = self._require_album()
        if album is None:
            return []
        return self._library_update_service.pair_live(album)

    # ------------------------------------------------------------------
    # Manifest helpers
    # ------------------------------------------------------------------
    def set_cover(self, rel: str) -> bool:
        """Set the album cover to *rel* and persist the manifest."""

        album = self._require_album()
        if album is None:
            return False
        success = self._metadata_service.set_album_cover(album, rel)
        if success:
            self.albumCoverUpdated.emit(album.root, album.root / rel)
        return success

    def bind_library(self, library: "LibraryManager") -> None:
        """Remember the library manager so static collections stay in sync."""

        if self._library_manager is not None:
            try:
                self._library_manager.treeUpdated.disconnect(self._on_library_tree_updated)
                self._library_manager.scanProgress.disconnect(self._relay_scan_progress)
                self._library_manager.scanChunkReady.disconnect(self._relay_scan_chunk_ready)
                self._library_manager.scanFinished.disconnect(self._relay_scan_finished)
            except (RuntimeError, TypeError):
                pass

        self._library_manager = library
        self._library_update_service.reset_cache()
        self._library_manager.treeUpdated.connect(self._on_library_tree_updated)

        try:
            self._library_update_service.scanProgress.disconnect(self._relay_scan_progress)
            self._library_update_service.scanChunkReady.disconnect(self._relay_scan_chunk_ready)
            self._library_update_service.scanFinished.disconnect(self._relay_scan_finished)
        except (RuntimeError, TypeError):
            pass

        self._library_manager.scanProgress.connect(self._relay_scan_progress)
        self._library_manager.scanChunkReady.connect(self._relay_scan_chunk_ready)
        self._library_manager.scanFinished.connect(self._relay_scan_finished)
        self._library_manager.scanBatchFailed.connect(self._relay_scan_batch_failed)

        if self._library_manager.root():
            self._on_library_tree_updated()

    def _on_library_tree_updated(self) -> None:
        """Propagate library root updates."""
        pass  # Gallery state now lives behind the collection store + VM adapter path.

    def register_restore_prompt(
        self, handler: Optional[Callable[[str], bool]]
    ) -> None:
        """Register *handler* to confirm restore-to-root fallbacks."""
        self._restore_prompt_handler = handler

    def import_files(
        self,
        sources: Iterable[Path],
        *,
        destination: Optional[Path] = None,
        mark_featured: bool = False,
    ) -> None:
        """Import *sources* asynchronously and refresh the destination album."""

        self._import_service.import_files(
            sources,
            destination=destination,
            mark_featured=mark_featured,
        )

    def move_assets(self, sources: Iterable[Path], destination: Path) -> None:
        """Move *sources* into *destination* and refresh the relevant albums."""

        self._move_service.move_assets(sources, destination)

    def delete_assets(self, sources: Iterable[Path]) -> None:
        """Move *sources* into the dedicated deleted-items folder."""

        self._deletion_service.delete_assets(sources)

    def restore_assets(self, sources: Iterable[Path]) -> bool:
        """Return ``True`` when at least one trashed asset restore is scheduled."""

        return self._restoration_service.restore_assets(sources)

    def toggle_featured(self, ref: str) -> bool:
        """Toggle *ref* in the active album and mirror the change in the library."""

        album = self._require_album()
        if album is None or not ref:
            return False

        return self._metadata_service.toggle_featured(album, ref)

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------
    def _require_album(self) -> Optional[Album]:
        if self._current_album is None:
            self.errorRaised.emit("No album is currently open.")
            return None
        return self._current_album

    def _refresh_view(self, root: Path) -> None:
        """Reload *root* so UI models pick up the latest manifest changes."""

        try:
            refreshed = Album.open(root)
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            return

        self._current_album = refreshed
        self.albumOpened.emit(refreshed.root)
        self.loadStarted.emit(refreshed.root)
        self.loadFinished.emit(refreshed.root, True)

    def _current_album_root(self) -> Optional[Path]:
        if self._current_album is None:
            return None
        return self._current_album.root

    def _paths_equal(self, first: Path, second: Path) -> bool:
        if first == second:
            return True
        try:
            return first.resolve() == second.resolve()
        except OSError:
            return False

    def _get_library_manager(self) -> Optional["LibraryManager"]:
        return self._library_manager

    @Slot(Path, Path, list, bool, bool, bool, bool)
    def _handle_move_operation_completed(
        self,
        source_root: Path,
        destination_root: Path,
        moved_pairs: list,
        source_ok: bool,
        destination_ok: bool,
        is_trash_destination: bool,
        is_restore_operation: bool,
    ) -> None:
        """Preserve the legacy private API by delegating to the new service."""
        self._library_update_service.handle_move_operation_completed(
            source_root,
            destination_root,
            moved_pairs,
            source_ok,
            destination_ok,
            is_trash_destination,
            is_restore_operation,
        )

    @Slot(str)
    def _on_service_error(self, message: str) -> None:
        """Relay service-level failures through the facade-wide error signal."""

        self.errorRaised.emit(message)

    @Slot(Path, int, int)
    def _relay_scan_progress(self, root: Path, current: int, total: int) -> None:
        self.scanProgress.emit(root, current, total)

    @Slot(Path, list)
    def _relay_scan_chunk_ready(self, root: Path, chunk: List[dict]) -> None:
        self.scanChunkReady.emit(root, chunk)

    @Slot(Path, bool)
    def _relay_scan_finished(self, root: Path, success: bool) -> None:
        self.scanFinished.emit(root, success)

    @Slot(Path, int)
    def _relay_scan_batch_failed(self, root: Path, count: int) -> None:
        self.scanBatchFailed.emit(root, count)

    @Slot(Path)
    def _relay_index_updated(self, root: Path) -> None:
        self.indexUpdated.emit(root)

    @Slot(Path)
    def _relay_links_updated(self, root: Path) -> None:
        self.linksUpdated.emit(root)

    @Slot(Path, bool, bool)
    def _on_asset_reload_requested(
        self,
        root: Path,
        announce_index: bool,
        force_reload: bool,
    ) -> None:
        # Legacy reload hook
        self.loadStarted.emit(root)
        self.loadFinished.emit(root, True)


__all__ = ["AppFacade"]
