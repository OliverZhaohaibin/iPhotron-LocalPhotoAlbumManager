"""Qt-aware facade that bridges the CLI backend to the GUI layer.

This module is the presentation-layer combinator.  Business logic lives in
the three sub-facades:

* :class:`~iPhoto.presentation.qt.facade.album_facade.AlbumFacade`
* :class:`~iPhoto.presentation.qt.facade.asset_facade.AssetFacade`
* :class:`~iPhoto.presentation.qt.facade.library_facade.LibraryFacade`

``AppFacade`` only maintains Qt signals, instantiates those facades, and
forwards every public method call to the appropriate sub-facade.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from .. import app as backend
from ..errors import IPhotoError
from ..models.album import Album
from ..presentation.qt.facade.album_facade import AlbumFacade
from ..presentation.qt.facade.asset_facade import AssetFacade
from ..presentation.qt.facade.library_facade import LibraryFacade
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
    """Combinator facade: owns Qt signals and aggregates sub-facades."""

    albumOpened = Signal(Path)
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
        self._current_album: Album | None = None
        self._pending_index_announcements: set[Path] = set()
        self._library_manager: LibraryManager | None = None
        self._restore_prompt_handler: Callable[[str], bool] | None = None
        self._model_provider: Callable[[], Any] | None = None

        def _pause_watcher() -> None:
            manager = self._library_manager
            if manager is not None:
                manager.pause_watcher()

        def _resume_watcher() -> None:
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

        # ------------------------------------------------------------------
        # Sub-facades
        # ------------------------------------------------------------------
        self._album_facade = AlbumFacade(
            backend_bridge=backend,
            metadata_service=self._metadata_service,
            library_update_service=self._library_update_service,
            current_album_getter=lambda: self._current_album,
            current_album_setter=self._set_current_album,
            library_manager_getter=self._get_library_manager,
            error_emitter=self.errorRaised.emit,
            album_opened_emitter=self.albumOpened.emit,
            load_started_emitter=self.loadStarted.emit,
            load_finished_emitter=self.loadFinished.emit,
            rescan_trigger=self.rescan_current_async,
        )

        self._asset_facade = AssetFacade(
            import_service=self._import_service,
            move_service=self._move_service,
            deletion_service=self._deletion_service,
            restoration_service=self._restoration_service,
        )

        self._library_facade = LibraryFacade(
            library_update_service=self._library_update_service,
            task_manager=self._task_manager,
            current_album_getter=lambda: self._current_album,
            library_manager_getter=self._get_library_manager,
            error_emitter=self.errorRaised.emit,
        )

    def set_model_provider(self, provider: Callable[[], Any]):
        """Inject the new ViewModel provider for legacy operations."""
        self._model_provider = provider

    # ------------------------------------------------------------------
    # Album lifecycle
    # ------------------------------------------------------------------
    @property
    def current_album(self) -> Album | None:
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
    def library_manager(self) -> LibraryManager | None:
        """Expose the underlying library manager."""

        return self._library_manager

    def open_album(self, root: Path) -> Album | None:
        """Open *root* and trigger background work as needed."""

        return self._album_facade.open_album(root)

    def rescan_current(self) -> list[dict]:
        """Rescan the active album and emit ``indexUpdated`` when done."""

        return self._library_facade.rescan_current()

    def rescan_current_async(self) -> None:
        """Start a background rescan for the active album."""

        self._library_facade.rescan_current_async()

    def _inject_scan_dependencies_for_tests(
        self,
        *,
        library_manager: LibraryManager | None = None,
        library_update_service: LibraryUpdateService | None = None,
    ) -> None:
        """Override scan collaborators during testing."""

        if library_manager is not None:
            self._library_manager = library_manager
        if library_update_service is not None:
            self._library_update_service = library_update_service
            self._library_facade.replace_library_update_service(library_update_service)
            self._album_facade.replace_library_update_service(library_update_service)

    def cancel_active_scans(self) -> None:
        """Request cancellation of any in-flight scan operations."""

        self._library_facade.cancel_active_scans()

    def is_performing_background_operation(self) -> bool:
        """Return ``True`` while imports or moves are still running."""

        return self._task_manager.has_watcher_blocking_tasks()

    def pair_live_current(self) -> list[dict]:
        """Rebuild Live Photo pairings for the active album."""

        return self._album_facade.pair_live_current()

    # ------------------------------------------------------------------
    # Manifest helpers
    # ------------------------------------------------------------------
    def set_cover(self, rel: str) -> bool:
        """Set the album cover to *rel* and persist the manifest."""

        return self._album_facade.set_cover(rel)

    def bind_library(self, library: LibraryManager) -> None:
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
        # ViewModels handle this via AssetDataSource now

    def register_restore_prompt(
        self, handler: Callable[[str], bool] | None
    ) -> None:
        """Register *handler* to confirm restore-to-root fallbacks."""
        self._restore_prompt_handler = handler

    def import_files(
        self,
        sources: Iterable[Path],
        *,
        destination: Path | None = None,
        mark_featured: bool = False,
    ) -> None:
        """Import *sources* asynchronously and refresh the destination album."""

        self._asset_facade.import_files(
            sources, destination=destination, mark_featured=mark_featured
        )

    def move_assets(self, sources: Iterable[Path], destination: Path) -> None:
        """Move *sources* into *destination* and refresh the relevant albums."""

        self._asset_facade.move_assets(sources, destination)

    def delete_assets(self, sources: Iterable[Path]) -> None:
        """Move *sources* into the dedicated deleted-items folder."""

        self._asset_facade.delete_assets(sources)

    def restore_assets(self, sources: Iterable[Path]) -> bool:
        """Return ``True`` when at least one trashed asset restore is scheduled."""

        return self._asset_facade.restore_assets(sources)

    def toggle_featured(self, ref: str) -> bool:
        """Toggle *ref* in the active album and mirror the change in the library."""

        return self._album_facade.toggle_featured(ref)

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------
    def _set_current_album(self, album: Album | None) -> None:
        """Update the currently active album (called by AlbumFacade)."""
        self._current_album = album

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

    def _current_album_root(self) -> Path | None:
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

    def _get_library_manager(self) -> LibraryManager | None:
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
    def _relay_scan_chunk_ready(self, root: Path, chunk: list[dict]) -> None:
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
