"""Qt-aware facade that bridges the CLI backend to the GUI layer."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple, TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from .. import app as backend
from ..cache.index_store import get_global_repository
from ..config import DEFAULT_INCLUDE, DEFAULT_EXCLUDE, WORK_DIR_NAME
from ..errors import AlbumOperationError, IPhotoError
from ..models.album import Album
from ..utils.jsonio import read_json
from ..utils.logging import get_logger
from .background_task_manager import BackgroundTaskManager
from .services import (
    AlbumMetadataService,
    AssetImportService,
    AssetMoveService,
    LibraryUpdateService,
)

if TYPE_CHECKING:
    from ..library.manager import LibraryManager
    from ..cache.index_store.repository import AssetRepository

import logging
logger = logging.getLogger(__name__)

class AppFacade(QObject):
    """Expose high-level album operations to the GUI layer."""

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
        return self._metadata_service.set_album_cover(album, rel)

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
        pass # ViewModels handle this via AssetDataSource now

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

        library = self._get_library_manager()
        if library is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return

        try:
            deleted_root = library.ensure_deleted_directory()
        except AlbumOperationError as exc:
            self.errorRaised.emit(str(exc))
            return

        def _normalize(path: Path) -> Path:
            try:
                return path.resolve()
            except OSError:
                return path

        normalized: List[Path] = []
        seen: Set[str] = set()
        for raw_path in sources:
            candidate = _normalize(Path(raw_path))
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)

        if not normalized:
            return

        # Use model provider to get live motion
        model = self._model_provider() if self._model_provider else None

        for still_path in list(normalized):
            metadata = None
            if model and hasattr(model, "metadata_for_path"):
                metadata = model.metadata_for_path(still_path)

            if not metadata or not metadata.get("is_live"):
                continue
            motion_raw = metadata.get("live_motion_abs")
            if not motion_raw:
                continue
            motion_path = _normalize(Path(str(motion_raw)))
            motion_key = str(motion_path)
            if motion_key not in seen:
                seen.add(motion_key)
                normalized.append(motion_path)

        self._move_service.move_assets(normalized, deleted_root, operation="delete")

    def restore_assets(self, sources: Iterable[Path]) -> bool:
        """Return ``True`` when at least one trashed asset restore is scheduled."""

        library = self._get_library_manager()
        if library is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return False

        library_root = library.root()
        if library_root is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return False

        trash_root = library.deleted_directory()
        if trash_root is None:
            self.errorRaised.emit("Recently Deleted folder is unavailable.")
            return False

        def _normalize(path: Path) -> Path:
            try:
                return path.resolve()
            except OSError:
                return path

        normalized: List[Path] = []
        seen: Set[str] = set()
        for raw_path in sources:
            candidate = _normalize(Path(raw_path))
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if not candidate.exists():
                self.errorRaised.emit(f"File not found: {candidate}")
                continue
            try:
                candidate.relative_to(trash_root)
            except ValueError:
                self.errorRaised.emit(
                    f"Selection is outside Recently Deleted: {candidate}"
                )
                continue
            normalized.append(candidate)

        if not normalized:
            return False

        model = self._model_provider() if self._model_provider else None

        for still_path in list(normalized):
            metadata = None
            if model and hasattr(model, "metadata_for_path"):
                metadata = model.metadata_for_path(still_path)

            if not metadata or not metadata.get("is_live"):
                continue
            motion_raw = metadata.get("live_motion_abs")
            if not motion_raw:
                continue
            motion_path = _normalize(Path(str(motion_raw)))
            motion_key = str(motion_path)
            if motion_key not in seen and motion_path.exists():
                seen.add(motion_key)
                try:
                    motion_path.relative_to(trash_root)
                except ValueError:
                    continue
                normalized.append(motion_path)

        try:
            trash_resolved = trash_root.resolve()
        except OSError:
            trash_resolved = trash_root
        try:
            library_resolved = library_root.resolve()
        except OSError:
            library_resolved = library_root
        try:
            album_path = trash_resolved.relative_to(library_resolved).as_posix()
        except ValueError:
            album_path = None
        store = get_global_repository(library_root)
        if album_path:
            index_rows = list(store.read_album_assets(album_path, include_subalbums=True))
        else:
            index_rows = list(store.read_all())
        row_lookup: Dict[str, dict] = {}
        for row in index_rows:
            if not isinstance(row, dict):
                continue
            rel_value = row.get("rel")
            if not isinstance(rel_value, str):
                continue
            candidate_path = library_root / rel_value
            key = str(_normalize(candidate_path))
            row_lookup[key] = row

        grouped: Dict[Path, List[Path]] = defaultdict(list)
        for path in normalized:
            try:
                key = str(_normalize(path))
                row = row_lookup.get(key)
                if not row:
                    raise LookupError("metadata unavailable")
                destination_root = self._determine_restore_destination(
                    row=row,
                    library=library,
                    library_root=library_root,
                    filename=path.name,
                )
                if destination_root is None:
                    continue
                destination_root.mkdir(parents=True, exist_ok=True)
            except LookupError:
                self.errorRaised.emit(
                    f"Missing index metadata for {path.name}; skipping restore."
                )
                continue
            except OSError as exc:
                self.errorRaised.emit(
                    f"Could not prepare restore destination '{destination_root}': {exc}"
                )
                continue
            grouped[destination_root].append(path)

        if not grouped:
            return False

        scheduled_restore = False
        for destination_root, paths in grouped.items():
            self._move_service.move_assets(
                paths,
                destination_root,
                operation="restore",
            )
            scheduled_restore = True

        return scheduled_restore

    def _determine_restore_destination(
        self,
        *,
        row: dict,
        library: "LibraryManager",
        library_root: Path,
        filename: str,
    ) -> Optional[Path]:
        """Return the directory that should receive a restored asset."""

        def _offer_restore_to_root(
            skip_reason: str,
            decline_reason: str,
        ) -> Optional[Path]:
            prompt = self._restore_prompt_handler
            if prompt is None:
                self.errorRaised.emit(skip_reason)
                return None
            if prompt(filename):
                return library_root
            self.errorRaised.emit(decline_reason)
            return None

        original_rel = row.get("original_rel_path")
        if isinstance(original_rel, str) and original_rel:
            candidate_path = library_root / original_rel
            try:
                candidate_path.relative_to(library_root)
            except ValueError:
                pass
            else:
                parent_dir = candidate_path.parent
                if parent_dir.exists():
                    return parent_dir

        album_id = row.get("original_album_id")
        subpath = row.get("original_album_subpath")
        if isinstance(album_id, str) and album_id and isinstance(subpath, str) and subpath:
            node = library.find_album_by_uuid(album_id)
            if node is not None:
                subpath_obj = Path(subpath)
                if subpath_obj.is_absolute() or any(part == ".." for part in subpath_obj.parts):
                    destination_root = node.path
                else:
                    destination_path = node.path / subpath_obj
                    try:
                        destination_path.relative_to(node.path)
                    except ValueError:
                        destination_root = node.path
                    else:
                        destination_root = destination_path.parent
                return destination_root

            return _offer_restore_to_root(
                skip_reason=(
                    f"Original album for {filename} no longer exists; skipping restore."
                ),
                decline_reason=(
                    f"Restore cancelled for {filename} because its original album is unavailable."
                ),
            )

        if isinstance(original_rel, str) and original_rel:
            return _offer_restore_to_root(
                skip_reason=(
                    f"Original album metadata is unavailable for {filename}; skipping restore."
                ),
                decline_reason=(
                    f"Restore cancelled for {filename} because you opted against placing it in the Basic Library root."
                ),
            )
        return _offer_restore_to_root(
            skip_reason=(
                f"Original location is unknown for {filename}; skipping restore."
            ),
            decline_reason=(
                f"Restore cancelled for {filename} because you opted against placing it in the Basic Library root."
            ),
        )

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
