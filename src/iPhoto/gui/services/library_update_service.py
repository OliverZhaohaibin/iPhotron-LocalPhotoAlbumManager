"""Service that orchestrates library scans and index synchronisation for the GUI."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ...application.services.library_reload_service import LibraryReloadService
from ...application.services.move_aftercare_service import MoveAftercareService
from ...application.services.move_bookkeeping_service import MoveBookkeepingService
from ...application.services.restore_aftercare_service import RestoreAftercareService
from ...application.use_cases.scan.merge_trash_restore_metadata_use_case import (
    MergeTrashRestoreMetadataUseCase,
)
from ...application.use_cases.scan.pair_live_photos_use_case_v2 import PairLivePhotosUseCaseV2
from ...application.use_cases.scan.persist_scan_result_use_case import PersistScanResultUseCase
from ...application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase
from ...config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from ...errors import IPhotoError
from ...index_sync_service import (
    ensure_links as _ensure_links,
)
from ...index_sync_service import (
    update_index_snapshot as _update_index_snapshot,
)

# Updated imports to new location
from ...library.workers.rescan_worker import RescanSignals, RescanWorker
from ...library.workers.scanner_worker import ScannerSignals, ScannerWorker
from ..background_task_manager import BackgroundTaskManager

if TYPE_CHECKING:
    from ...library.manager import LibraryManager
    from ...models.album import Album


@dataclass
class MoveOperationResult:
    """Consolidated result of a move / delete / restore operation.

    Emitted via :pyattr:`LibraryUpdateService.moveOperationCompleted` so that
    listeners can perform incremental updates instead of a full reload.
    """

    source_root: Path
    destination_root: Path
    moved_pairs: list[tuple[Path, Path]] = field(default_factory=list)
    removed_rels: list[str] = field(default_factory=list)
    added_rels: list[str] = field(default_factory=list)
    is_delete: bool = False
    is_restore: bool = False
    source_ok: bool = True
    destination_ok: bool = True


class LibraryUpdateService(QObject):
    """Coordinate rescans, Live Photo pairing, and move aftermath bookkeeping."""

    scanProgress = Signal(Path, int, int)
    scanChunkReady = Signal(Path, list)
    scanFinished = Signal(Path, bool)
    indexUpdated = Signal(Path)
    linksUpdated = Signal(Path)
    assetReloadRequested = Signal(Path, bool, bool)
    errorRaised = Signal(str)
    # Unified signal carrying a :class:`MoveOperationResult` so listeners can
    # perform incremental / diff-based updates (Plan 1 §5.2).
    moveOperationCompleted = Signal(object)

    def __init__(
        self,
        *,
        task_manager: BackgroundTaskManager,
        current_album_getter: Callable[[], Album | None],
        library_manager_getter: Callable[[], LibraryManager | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._task_manager = task_manager
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter
        self._scanner_worker: ScannerWorker | None = None
        self._scan_pending = False
        self._model_loading_due_to_scan = False

        def _get_library_root() -> Path | None:
            lib = self._library_manager_getter()
            return lib.root() if lib is not None else None

        self._rescan_use_case = RescanAlbumUseCase(library_root_getter=_get_library_root)
        self._pair_use_case = PairLivePhotosUseCaseV2(library_root_getter=_get_library_root)
        self._persist_use_case = PersistScanResultUseCase(
            update_index_snapshot=_update_index_snapshot,
            ensure_links=_ensure_links,
            library_root_getter=_get_library_root,
        )
        self._merge_trash_uc = MergeTrashRestoreMetadataUseCase()
        self._move_bookkeeping = MoveBookkeepingService()

        from ...application.policies.library_scope_policy import LibraryScopePolicy
        from ...application.services.trash_service import TrashService
        self._scope_policy = LibraryScopePolicy()
        self._trash_service = TrashService()

        # Phase 3 application services – own business rules extracted from this Qt service.
        self._move_aftercare = MoveAftercareService()
        self._restore_aftercare = RestoreAftercareService()
        self._reload_service = LibraryReloadService()

    # ------------------------------------------------------------------
    # Public API used by :class:`~iPhoto.gui.facade.AppFacade`
    # ------------------------------------------------------------------
    def rescan_album(self, album: Album) -> list[dict]:
        """Synchronously rebuild the album index and emit cache updates."""

        try:
            rows = self._rescan_use_case.execute(album.root)
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            return []

        self.indexUpdated.emit(album.root)
        self.linksUpdated.emit(album.root)
        return rows

    def rescan_album_async(self, album: Album) -> None:
        """Start an asynchronous rescan for *album* using the background pool."""

        library_root = None
        lib_manager = self._library_manager_getter()
        if lib_manager:
            library_root = lib_manager.root()

        if self._scanner_worker is not None:
            self._scanner_worker.cancel()
            self._scan_pending = True
            return

        filters = album.manifest.get("filters", {}) if isinstance(album.manifest, dict) else {}
        include: Iterable[str] = filters.get("include", DEFAULT_INCLUDE)
        exclude: Iterable[str] = filters.get("exclude", DEFAULT_EXCLUDE)

        signals = ScannerSignals()
        signals.progressUpdated.connect(self._relay_scan_progress)
        signals.chunkReady.connect(self._relay_scan_chunk_ready)

        worker = ScannerWorker(
            album.root,
            include,
            exclude,
            signals,
            library_root=library_root,
        )
        self._scanner_worker = worker
        self._scan_pending = False

        self._task_manager.submit_task(
            task_id=f"scan:{album.root}",
            worker=worker,
            progress=signals.progressUpdated,
            finished=signals.finished,
            error=signals.error,
            pause_watcher=False,
            on_finished=lambda root, rows, captured_library_root=library_root: self._on_scan_finished(
                worker,
                root,
                rows,
                library_root=captured_library_root,
            ),
            on_error=lambda root, message: self._on_scan_error(worker, root, message),
            result_payload=lambda root, rows: rows,
        )

    def cancel_active_scan(self) -> None:
        """Request cancellation of the active scan without scheduling retries."""

        if self._scanner_worker is None:
            return

        self._scanner_worker.cancel()
        # Cancelling a scan should not schedule immediate retry attempts.
        self._scan_pending = False

    def pair_live(self, album: Album) -> list[dict]:
        """Rebuild Live Photo pairings for *album* and refresh related views."""

        try:
            groups = self._pair_use_case.execute(album.root)
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            return []

        self.linksUpdated.emit(album.root)
        self.assetReloadRequested.emit(album.root, False, False)
        return [g.__dict__ for g in groups]

    def announce_album_refresh(
        self,
        root: Path,
        *,
        request_reload: bool = True,
        force_reload: bool = False,
        announce_index: bool = False,
    ) -> None:
        """Emit index refresh signals for *root* and optionally request a reload."""

        normalised = Path(root)
        self.indexUpdated.emit(normalised)
        self.linksUpdated.emit(normalised)
        if request_reload:
            self.assetReloadRequested.emit(normalised, announce_index, force_reload)

    def consume_forced_reload(self, root: Path) -> bool:
        """Return ``True`` if *root* was marked for a forced reload."""
        return self._move_bookkeeping.consume_forced_reload(root)

    def reset_cache(self) -> None:
        """Drop cached album resolution results after library re-binding."""
        self._move_aftercare.reset()
        self._move_bookkeeping.reset()

    # ------------------------------------------------------------------
    # Slots wired from :class:`AssetMoveService`
    # ------------------------------------------------------------------
    @Slot(Path, Path, list, bool, bool, bool, bool)
    def handle_move_operation_completed(
        self,
        source_root: Path,
        destination_root: Path,
        moved_pairs_raw: list,
        source_ok: bool,
        destination_ok: bool,
        _is_trash_destination: bool,
        is_restore_operation: bool,
    ) -> None:
        """Refresh impacted album views after assets have been moved."""

        moved_pairs: list[tuple[Path, Path]] = []
        for entry in moved_pairs_raw:
            if isinstance(entry, (tuple, list)) and len(entry) == 2:
                moved_pairs.append((Path(entry[0]), Path(entry[1])))

        if not moved_pairs:
            return

        library = self._library_manager()
        library_root = library.root() if library is not None else None

        current_album = self._current_album_getter()
        current_root = current_album.root if current_album is not None else None

        # Delegate move aftermath computation to the application service.
        aftermath = self._move_aftercare.compute_aftermath(
            moved_pairs,
            source_root,
            destination_root,
            current_root,
            library_root,
            source_ok=source_ok,
            destination_ok=destination_ok,
        )

        result = MoveOperationResult(
            source_root=source_root,
            destination_root=destination_root,
            moved_pairs=moved_pairs,
            removed_rels=aftermath.removed_rels,
            added_rels=aftermath.added_rels,
            is_delete=bool(_is_trash_destination and not is_restore_operation),
            is_restore=is_restore_operation,
            source_ok=source_ok,
            destination_ok=destination_ok,
        )
        self.moveOperationCompleted.emit(result)

        for candidate, should_restart in aftermath.refresh_targets.values():
            self.indexUpdated.emit(candidate)
            self.linksUpdated.emit(candidate)
            if should_restart:
                force_reload = self._move_aftercare.consume_forced_reload(candidate)
                self.assetReloadRequested.emit(current_root, False, force_reload)

        # Delegate post-restore rescan eligibility to the application service.
        trash_root = library.deleted_directory() if library is not None else None
        if not self._restore_aftercare.should_trigger_restore_rescan(
            is_restore_operation=is_restore_operation,
            destination_ok=destination_ok,
            source_root=source_root,
            trash_root=trash_root,
        ):
            return

        restore_targets = self._restore_aftercare.compute_restore_rescan_targets(
            moved_pairs, library_root
        )
        for album_root in restore_targets:
            self._refresh_restored_album(album_root, library_root)

    # ------------------------------------------------------------------
    # Internal helpers for scan management
    # ------------------------------------------------------------------
    def _relay_scan_progress(self, root: Path, current: int, total: int) -> None:
        """Forward worker progress updates to keep Qt's type system satisfied."""

        self.scanProgress.emit(root, current, total)

    def _relay_scan_chunk_ready(self, root: Path, chunk: list[dict]) -> None:
        """Forward worker chunks to listeners."""

        self.scanChunkReady.emit(root, chunk)

    def _on_scan_finished(
        self,
        worker: ScannerWorker,
        root: Path,
        rows: Sequence[dict],
        *,
        library_root: Path | None = None,
    ) -> None:
        if self._scanner_worker is not worker:
            return

        if worker.cancelled:
            self.scanFinished.emit(root, True)
            should_restart = self._scan_pending
            self._cleanup_scan_worker()
            if should_restart:
                self._schedule_scan_retry()
            return

        if worker.failed:
            self.scanFinished.emit(root, False)
            should_restart = self._scan_pending
            self._cleanup_scan_worker()
            if should_restart:
                self._schedule_scan_retry()
            return

        if library_root is None:
            library_root = getattr(worker, "library_root", None)

        materialised_rows = list(rows)

        # Delegate trash-restore metadata merge to the application use case.
        # This removes the inline business logic from the Qt service layer.
        materialised_rows = self._merge_trash_uc.execute(
            materialised_rows, root, library_root
        )

        try:
            # Persist the freshly computed index snapshot immediately so future
            # reloads observe the new metadata rather than the stale cache that
            # existed before the rescan.  The worker keeps the result in memory,
            # therefore we flush the global index and ``links.json`` here to
            # mirror the historical facade behaviour before notifying listeners.
            self._persist_use_case.execute(root, materialised_rows, library_root=library_root)
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            self.scanFinished.emit(root, False)
        else:
            self.indexUpdated.emit(root)
            self.linksUpdated.emit(root)
            # Delegate scan reload decision to the application-layer reload service.
            action = self._reload_service.compute_scan_reload_action(
                root,
                self._current_album_root(),
                model_loading_due_to_scan=self._model_loading_due_to_scan,
            )
            self._model_loading_due_to_scan = False
            if action.requires_action:
                self.assetReloadRequested.emit(root, False, False)
            self.scanFinished.emit(root, True)

        should_restart = self._scan_pending
        self._cleanup_scan_worker()

        if should_restart:
            self._schedule_scan_retry()

    def _on_scan_error(
        self,
        worker: ScannerWorker,
        root: Path,
        message: str,
    ) -> None:
        if self._scanner_worker is not worker:
            return

        self.errorRaised.emit(message)
        self.scanFinished.emit(root, False)

        should_restart = self._scan_pending
        self._cleanup_scan_worker()

        if should_restart:
            self._schedule_scan_retry()

    def _cleanup_scan_worker(self) -> None:
        self._scanner_worker = None
        self._scan_pending = False

    def _schedule_scan_retry(self) -> None:
        QTimer.singleShot(0, self._retry_scan_if_album_available)

    def _retry_scan_if_album_available(self) -> None:
        album = self._current_album_getter()
        if album is None:
            return
        self.rescan_album_async(album)

    # ------------------------------------------------------------------
    # Album bookkeeping helpers
    # ------------------------------------------------------------------
    def _current_album_root(self) -> Path | None:
        album = self._current_album_getter()
        return album.root if album is not None else None

    def _library_manager(self) -> LibraryManager | None:
        return self._library_manager_getter()

    def _refresh_restored_album(self, album_root: Path, library_root: Path | None) -> None:
        album_root = Path(album_root)
        if not album_root.exists():
            return

        signals = RescanSignals()
        worker = RescanWorker(album_root, signals, library_root=library_root)
        task_id = self._build_restore_rescan_task_id(album_root)

        def _on_finished(path: Path, succeeded: bool) -> None:
            if not succeeded:
                return

            self.indexUpdated.emit(path)
            self.linksUpdated.emit(path)

            current_album = self._current_album_getter()
            current_root = current_album.root if current_album is not None else None

            # Delegate reload decision to the application-layer reload service.
            action = self._reload_service.compute_restore_reload_action(
                path, current_root, library_root
            )

            if action.should_reload_current:
                force_reload = self._move_aftercare.consume_forced_reload(path)
                self.assetReloadRequested.emit(current_root, False, force_reload)
            elif action.should_reload_as_library:
                self.assetReloadRequested.emit(current_root, False, False)

        def _on_error(path: Path, message: str) -> None:
            self.errorRaised.emit(f"Failed to refresh '{path.name}': {message}")

        self._task_manager.submit_task(
            task_id=task_id,
            worker=worker,
            finished=signals.finished,
            error=signals.error,
            pause_watcher=False,
            on_finished=_on_finished,
            on_error=_on_error,
            result_payload=lambda path, succeeded: (path, succeeded),
        )

    def _build_restore_rescan_task_id(self, album_root: Path) -> str:
        try:
            normalised = album_root.resolve()
        except OSError:
            normalised = album_root
        return f"restore-rescan:{normalised}:{uuid.uuid4().hex}"


__all__ = ["LibraryUpdateService", "MoveOperationResult"]
