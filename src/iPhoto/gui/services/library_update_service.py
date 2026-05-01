"""Service that orchestrates library scans and index synchronisation for the GUI."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple, TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ...bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService
from ...bootstrap.library_scan_service import LibraryScanService
from ...config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from ...errors import IPhotoError
from ...utils.pathutils import resolve_work_dir
from ..background_task_manager import BackgroundTaskManager
from .library_update_tasks import LibraryUpdateTaskRunner, ScanTaskCompletion

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
    moved_pairs: List[Tuple[Path, Path]] = field(default_factory=list)
    removed_rels: List[str] = field(default_factory=list)
    added_rels: List[str] = field(default_factory=list)
    is_delete: bool = False
    is_restore: bool = False
    source_ok: bool = True
    destination_ok: bool = True


@dataclass(frozen=True)
class AlbumOpenRouting:
    """Session-backed album-open preparation result for GUI callers."""

    asset_count: int
    should_rescan_async: bool = False


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
        current_album_getter: Callable[[], Optional["Album"]],
        library_manager_getter: Callable[[], Optional["LibraryManager"]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._task_runner = LibraryUpdateTaskRunner(task_manager=task_manager)
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter
        self._stale_album_roots: Dict[str, Path] = {}
        self._album_root_cache: Dict[str, Optional[Path]] = {}
        self._model_loading_due_to_scan = False

    # ------------------------------------------------------------------
    # Public API used by :class:`~iPhoto.gui.facade.AppFacade`
    # ------------------------------------------------------------------
    def prepare_album_open(
        self,
        root: Path,
        *,
        autoscan: bool = False,
        hydrate_index: bool = False,
        sync_manifest_favorites: bool = False,
    ) -> AlbumOpenRouting:
        """Prepare one album open through the active session scan surface."""

        scan_root = Path(root)
        library_root, scan_service, _lifecycle_service = self._scan_dependencies(scan_root)
        active_scan_service = scan_service or LibraryScanService(library_root or scan_root)
        preparation = active_scan_service.prepare_album_open(
            scan_root,
            autoscan=autoscan,
            hydrate_index=hydrate_index,
            sync_manifest_favorites=sync_manifest_favorites,
        )

        library = self._library_manager()
        is_already_scanning = bool(
            library is not None and library.is_scanning_path(scan_root)
        )
        should_rescan_async = (
            preparation.asset_count == 0
            and not preparation.scanned
            and not is_already_scanning
        )
        return AlbumOpenRouting(
            asset_count=preparation.asset_count,
            should_rescan_async=should_rescan_async,
        )

    def rescan_album(self, album: "Album") -> List[dict]:
        """Synchronously rebuild the album index and emit cache updates."""

        library_root, scan_service = self._scan_dependencies(album.root)
        active_scan_service = scan_service or LibraryScanService(
            library_root or album.root
        )

        try:
            rows = active_scan_service.rescan_album(
                album.root,
                sync_manifest_favorites=scan_service is None and library_root is None,
                pair_live=True,
            )
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            return []

        self.indexUpdated.emit(album.root)
        self.linksUpdated.emit(album.root)
        return rows

    def rescan_album_async(self, album: "Album") -> None:
        """Start an asynchronous rescan for *album* using the background pool."""

        include, exclude = self._scan_filters(album)
        lib_manager = self._library_manager()
        if lib_manager is not None:
            lib_manager.start_scanning(album.root, include, exclude)
            return

        library_root, scan_service = self._scan_dependencies(album.root)
        self._task_runner.start_scan(
            root=album.root,
            include=include,
            exclude=exclude,
            library_root=library_root,
            scan_service=scan_service,
            on_progress=self._relay_scan_progress,
            on_chunk=self._relay_scan_chunk_ready,
            on_cancelled=self._on_scan_cancelled,
            on_completed=self._on_scan_completed,
            on_error=self._on_scan_error,
        )

    def cancel_active_scan(self) -> None:
        """Request cancellation of the active scan without scheduling retries."""

        self._task_runner.cancel_active_scan()

    def pair_live(self, album: "Album") -> List[dict]:
        """Rebuild Live Photo pairings for *album* and refresh related views."""

        library_root, scan_service = self._scan_dependencies(album.root)

        try:
            active_scan_service = scan_service or LibraryScanService(
                library_root or album.root
            )
            groups = active_scan_service.pair_album(album.root)
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            return []

        self.linksUpdated.emit(album.root)
        self.assetReloadRequested.emit(album.root, False, False)
        return [group.__dict__ for group in groups]

    def handle_media_load_failure(self, path: Path) -> Path | None:
        """Repair index/link state after an asset can no longer be decoded."""

        target = Path(path)
        library = self._library_manager()
        library_root = library.root() if library is not None else None
        scan_service = getattr(library, "scan_service", None) if library is not None else None
        repair_root = library_root or self._standalone_missing_asset_root(target)
        lifecycle_service = (
            getattr(library, "asset_lifecycle_service", None)
            if library is not None
            else None
        )
        lifecycle_root = getattr(lifecycle_service, "library_root", None)
        if lifecycle_service is None or (
            library_root is None
            and (
                lifecycle_root is None
                or not self._paths_equal(Path(lifecycle_root), repair_root)
            )
        ):
            lifecycle_service = LibraryAssetLifecycleService(
                repair_root,
                scan_service=scan_service,
            )
        refresh_root = lifecycle_service.repair_missing_asset(target)
        if refresh_root is not None and library_root is None:
            refresh_root = repair_root
        if refresh_root is not None:
            self.indexUpdated.emit(refresh_root)
            self.linksUpdated.emit(refresh_root)
        return refresh_root

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

        return self._consume_forced_reload(root)

    def reset_cache(self) -> None:
        """Drop cached album resolution results after library re-binding."""

        self._album_root_cache.clear()
        self._stale_album_roots.clear()

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

        moved_pairs: List[Tuple[Path, Path]] = []
        for entry in moved_pairs_raw:
            if isinstance(entry, (tuple, list)) and len(entry) == 2:
                moved_pairs.append((Path(entry[0]), Path(entry[1])))

        if not moved_pairs:
            return

        library = self._library_manager()
        library_root = library.root() if library is not None else None

        # --- Emit unified result signal (Plan 1 §5.2) ---
        removed_rels: List[str] = []
        added_rels: List[str] = []
        base = library_root if library_root else source_root
        for original, target in moved_pairs:
            try:
                removed_rels.append(original.resolve().relative_to(base.resolve()).as_posix())
            except (OSError, ValueError):
                pass
            dest_base = library_root if library_root else destination_root
            try:
                added_rels.append(target.resolve().relative_to(dest_base.resolve()).as_posix())
            except (OSError, ValueError):
                pass

        result = MoveOperationResult(
            source_root=source_root,
            destination_root=destination_root,
            moved_pairs=moved_pairs,
            removed_rels=removed_rels,
            added_rels=added_rels,
            is_delete=bool(_is_trash_destination and not is_restore_operation),
            is_restore=is_restore_operation,
            source_ok=source_ok,
            destination_ok=destination_ok,
        )
        self.moveOperationCompleted.emit(result)

        # --- Legacy signal cascade (kept for backward compatibility) ---
        current_album = self._current_album_getter()
        current_root = current_album.root if current_album is not None else None

        refresh_targets: Dict[str, Tuple[Path, bool]] = {}
        blocked_restarts: Set[str] = set()

        def _record_refresh(path: Optional[Path], *, allow_restart: bool = True) -> None:
            if path is None:
                return
            try:
                normalised = self._normalise_path(path)
            except ValueError:
                normalised = path
            key = str(normalised)
            self._mark_album_stale(path)
            if not allow_restart:
                blocked_restarts.add(key)
            should_restart = bool(
                allow_restart
                and key not in blocked_restarts
                and current_root is not None
                and self._paths_equal(current_root, path)
            )
            existing = refresh_targets.get(key)
            if existing is None or (not existing[1] and should_restart):
                refresh_targets[key] = (path, should_restart)

        if source_ok:
            _record_refresh(source_root, allow_restart=False)
        if destination_ok:
            _record_refresh(destination_root)

        additional_roots = self._collect_album_roots_from_pairs(moved_pairs)
        for extra_root in additional_roots:
            _record_refresh(extra_root)

        if library_root is not None:
            touched_library = False
            if source_ok and self._paths_equal(source_root, library_root):
                touched_library = True
            if destination_ok and self._paths_equal(destination_root, library_root):
                touched_library = True
            if not touched_library:
                for original, target in moved_pairs:
                    if self._path_is_descendant(original, library_root) or self._path_is_descendant(
                        target, library_root
                    ):
                        touched_library = True
                        break
            if touched_library:
                _record_refresh(library_root)

        for candidate, should_restart in refresh_targets.values():
            self.indexUpdated.emit(candidate)
            self.linksUpdated.emit(candidate)
            if should_restart:
                target_root = current_root if current_root and self._paths_equal(current_root, candidate) else candidate
                force_reload = self._consume_forced_reload(candidate)
                self.assetReloadRequested.emit(target_root, False, force_reload)

        if not is_restore_operation or not destination_ok:
            return

        if library is None:
            return

        trash_root = library.deleted_directory()
        if trash_root is None:
            return

        if not self._paths_equal(source_root, trash_root):
            return

        library_root_normalised = (
            self._normalise_path(library_root) if library_root is not None else None
        )

        unique_album_roots: Dict[str, Path] = {}
        for _, destination in moved_pairs:
            album_root = Path(destination).parent
            normalised_album = self._normalise_path(album_root)
            if library_root_normalised is not None:
                try:
                    normalised_album.relative_to(library_root_normalised)
                except ValueError:
                    continue
            key = str(normalised_album)
            if key not in unique_album_roots:
                unique_album_roots[key] = album_root

        for album_root in unique_album_roots.values():
            self._refresh_restored_album(album_root, library_root)

    # ------------------------------------------------------------------
    # Internal helpers for scan management
    # ------------------------------------------------------------------
    def _relay_scan_progress(self, root: Path, current: int, total: int) -> None:
        """Forward worker progress updates to keep Qt's type system satisfied."""

        self.scanProgress.emit(root, current, total)

    def _relay_scan_chunk_ready(self, root: Path, chunk: List[dict]) -> None:
        """Forward worker chunks to listeners."""

        self.scanChunkReady.emit(root, chunk)

    def _on_scan_cancelled(self, root: Path, restart_requested: bool) -> None:
        self.scanFinished.emit(root, True)
        if restart_requested:
            self._schedule_scan_retry()

    def _on_scan_completed(
        self,
        completion: ScanTaskCompletion,
    ) -> None:
        try:
            completion.scan_service.finalize_scan_result(
                completion.root,
                completion.rows,
                pair_live=True,
            )
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            self.scanFinished.emit(completion.root, False)
        else:
            self.indexUpdated.emit(completion.root)
            self.linksUpdated.emit(completion.root)
            # Ensure the view reloads if this scan was triggered for the current album
            # (e.g. initial auto-scan on startup).
            # Only emit assetReloadRequested if the model is not already loading due to this scan
            if not self._model_loading_due_to_scan:
                self.assetReloadRequested.emit(completion.root, False, False)
            self._model_loading_due_to_scan = False
            self.scanFinished.emit(completion.root, True)

        if completion.restart_requested:
            self._schedule_scan_retry()

    def _on_scan_error(
        self,
        root: Path,
        message: str,
        restart_requested: bool,
    ) -> None:
        self.errorRaised.emit(message)
        self.scanFinished.emit(root, False)

        if restart_requested:
            self._schedule_scan_retry()

    def _scan_dependencies(
        self,
        root: Path,
    ) -> tuple[
        Path | None,
        LibraryScanService | None,
    ]:
        library_root: Path | None = None
        scan_service: LibraryScanService | None = None
        library = self._library_manager()
        if library is not None:
            library_root = library.root()
            scan_service = getattr(library, "scan_service", None)
        if library_root is None:
            root = Path(root)
        return library_root, scan_service

    def _scan_filters(self, album: "Album") -> tuple[Iterable[str], Iterable[str]]:
        filters = album.manifest.get("filters", {}) if isinstance(album.manifest, dict) else {}
        include: Iterable[str] = filters.get("include", DEFAULT_INCLUDE)
        exclude: Iterable[str] = filters.get("exclude", DEFAULT_EXCLUDE)
        return include, exclude

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
    def _current_album_root(self) -> Optional[Path]:
        album = self._current_album_getter()
        return album.root if album is not None else None

    def _standalone_missing_asset_root(self, target: Path) -> Path:
        current_root = self._current_album_root()
        if current_root is not None and self._path_is_descendant(target, current_root):
            return current_root

        current = Path(target).parent
        while True:
            if resolve_work_dir(current) is not None:
                return current
            if current.parent == current:
                return Path(target).parent
            current = current.parent

    def _library_manager(self) -> Optional["LibraryManager"]:
        return self._library_manager_getter()

    def _mark_album_stale(self, path: Path) -> None:
        try:
            normalised = self._normalise_path(path)
        except ValueError:
            return
        self._stale_album_roots[str(normalised)] = path

    def _consume_forced_reload(self, path: Path) -> bool:
        try:
            normalised = self._normalise_path(path)
        except ValueError:
            return False
        key = str(normalised)
        if key not in self._stale_album_roots:
            return False
        self._stale_album_roots.pop(key, None)
        return True

    def _collect_album_roots_from_pairs(self, pairs: List[Tuple[Path, Path]]) -> Set[Path]:
        if not pairs:
            return set()

        library = self._library_manager()
        if library is None:
            return set()
        library_root = library.root()
        if library_root is None:
            return set()

        library_root_norm = self._normalise_path(library_root)

        affected: Set[Path] = set()
        for original, target in pairs:
            for candidate in (original, target):
                album_root = self._locate_album_root(candidate.parent, library_root_norm)
                if album_root is not None:
                    affected.add(album_root)
        return affected

    def _locate_album_root(self, start: Path, library_root: Path) -> Optional[Path]:
        try:
            candidate = self._normalise_path(start)
        except ValueError:
            candidate = start

        key = str(candidate)
        cached = self._album_root_cache.get(key, ...)
        if cached is not ...:
            return cached

        visited: List[Path] = []
        current = candidate
        while True:
            visited.append(current)
            if resolve_work_dir(current) is not None:
                album_root = current
                break
            if self._paths_equal(current, library_root) or current.parent == current:
                album_root = None
                break
            current = current.parent

        for entry in visited:
            self._album_root_cache[str(entry)] = album_root

        return album_root

    def _refresh_restored_album(self, album_root: Path, library_root: Optional[Path]) -> None:
        album_root = Path(album_root)
        if not album_root.exists():
            return

        library = self._library_manager()
        scan_service = getattr(library, "scan_service", None) if library is not None else None
        task_id = self._build_restore_rescan_task_id(album_root)

        def _on_finished(path: Path, succeeded: bool) -> None:
            if not succeeded:
                return

            self.indexUpdated.emit(path)
            self.linksUpdated.emit(path)

            current_album = self._current_album_getter()
            current_root = current_album.root if current_album is not None else None

            if current_root is not None and self._paths_equal(current_root, path):
                force_reload = self._consume_forced_reload(path)
                self.assetReloadRequested.emit(current_root, False, force_reload)
                return

            if (
                library_root is not None
                and current_root is not None
                and self._paths_equal(current_root, library_root)
                and self._path_is_descendant(path, library_root)
            ):
                self.assetReloadRequested.emit(current_root, False, False)

        def _on_error(path: Path, message: str) -> None:
            self.errorRaised.emit(f"Failed to refresh '{path.name}': {message}")

        self._task_runner.start_restore_refresh(
            root=album_root,
            task_id=task_id,
            library_root=library_root,
            scan_service=scan_service,
            on_finished=_on_finished,
            on_error=_on_error,
        )

    def _build_restore_rescan_task_id(self, album_root: Path) -> str:
        normalised = self._normalise_path(album_root)
        return f"restore-rescan:{normalised}:{uuid.uuid4().hex}"

    def _normalise_path(self, path: Optional[Path]) -> Path:
        if path is None:
            raise ValueError("Cannot normalise a null path.")
        try:
            return path.resolve()
        except OSError:
            return path

    def _paths_equal(self, left: Path, right: Path) -> bool:
        if left == right:
            return True
        return self._normalise_path(left) == self._normalise_path(right)

    def _path_is_descendant(self, candidate: Path, ancestor: Path) -> bool:
        try:
            candidate_norm = self._normalise_path(candidate)
            ancestor_norm = self._normalise_path(ancestor)
        except ValueError:
            return False

        if candidate_norm == ancestor_norm:
            return True

        try:
            candidate_norm.relative_to(ancestor_norm)
        except ValueError:
            return False
        return True


__all__ = ["LibraryUpdateService", "MoveOperationResult"]
