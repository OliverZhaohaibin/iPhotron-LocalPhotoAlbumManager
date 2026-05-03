"""Service dedicated to moving assets between albums on behalf of the facade."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

from ...bootstrap.library_asset_operation_service import (
    AssetMovePlan,
    LibraryAssetOperationService,
    MetadataLookup,
)
from ...bootstrap.service_factories import create_compat_asset_operation_service
from ..background_task_manager import BackgroundTaskManager
from ..ui.tasks.move_worker import MoveSignals, MoveWorker

if TYPE_CHECKING:
    from ...library.manager import LibraryManager
    from ...models.album import Album


class AssetMoveService(QObject):
    """Validate and execute asset move operations, surfacing progress events."""

    moveStarted = Signal(Path, Path)
    moveProgress = Signal(Path, int, int)
    moveFinished = Signal(Path, Path, bool, str)
    # ``moveCompletedDetailed`` mirrors the worker payload so higher-level
    # components (such as :class:`AppFacade`) can react to restore operations
    # with additional bookkeeping, e.g. refreshing album views.  Qt's signal
    # type system does not understand ``list[tuple[Path, Path]]`` so we emit
    # the raw ``list`` that contains :class:`pathlib.Path` pairs alongside the
    # worker flags.
    moveCompletedDetailed = Signal(Path, Path, list, bool, bool, bool, bool)
    errorRaised = Signal(str)

    def __init__(
        self,
        *,
        task_manager: BackgroundTaskManager,
        current_album_getter: Callable[[], Optional["Album"]],
        library_manager_getter: Callable[[], Optional["LibraryManager"]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._task_manager = task_manager
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter

    def move_assets(
        self,
        sources: Iterable[Path],
        destination: Path,
        *,
        operation: str = "move",
        metadata_lookup: MetadataLookup | None = None,
    ) -> bool:
        """Validate *sources* and queue a worker for the requested *operation*.

        ``operation`` accepts ``"move"`` (default), ``"delete"`` when moving items
        into Recently Deleted, and ``"restore"`` when returning files from the trash
        to their original albums.  The distinction matters because restore jobs must
        avoid annotating the destination index with ``original_rel_path`` entries
        while delete jobs must do the opposite.
        """

        album = self._current_album_getter()
        library_manager = self._library_manager_getter()
        operation_service = self._operation_service(library_manager)
        plan = operation_service.plan_move_request(
            sources,
            destination,
            current_album_root=album.root if album is not None else None,
            operation=operation,
            trash_root=self._deleted_directory(library_manager),
            metadata_lookup=metadata_lookup,
        )
        return self.submit_plan(plan)

    def submit_plan(self, plan: AssetMovePlan) -> bool:
        """Queue a prevalidated file-operation plan."""

        for message in plan.errors:
            self.errorRaised.emit(message)

        if not plan.accepted:
            if (
                plan.finished_message
                and plan.source_root is not None
                and plan.destination_root is not None
            ):
                self.moveFinished.emit(
                    plan.source_root,
                    plan.destination_root,
                    False,
                    plan.finished_message,
                )
            return False

        if plan.source_root is None or plan.destination_root is None:
            return False

        signals = MoveSignals()
        signals.started.connect(self._on_move_started)
        signals.progress.connect(self._on_move_progress)
        worker = MoveWorker(
            plan.sources,
            plan.source_root,
            plan.destination_root,
            signals,
            library_root=plan.library_root,
            trash_root=plan.trash_root,
            is_restore=plan.operation == "restore",
            asset_lifecycle_service=plan.asset_lifecycle_service,
        )
        unique_task_id = (
            f"move:{plan.operation}:{plan.source_root}->{plan.destination_root}:{uuid.uuid4().hex}"
        )
        # Move requests share their origin and target directories, so we need a unique
        # suffix on the identifier to allow queuing multiple operations without the
        # BackgroundTaskManager rejecting the submission as a duplicate.
        self._task_manager.submit_task(
            task_id=unique_task_id,
            worker=worker,
            started=signals.started,
            progress=signals.progress,
            finished=signals.finished,
            error=signals.error,
            pause_watcher=True,
            on_finished=lambda src, dest, moved, source_ok, destination_ok, *, move_worker=worker: self._handle_move_finished(
                src,
                dest,
                moved,
                source_ok,
                destination_ok,
                move_worker,
            ),
            on_error=self._handle_worker_error,
            result_payload=lambda src, dest, moved, *_: moved,
        )
        return True

    def _operation_service(
        self,
        library_manager: Optional["LibraryManager"],
    ) -> LibraryAssetOperationService:
        if library_manager is not None:
            candidate = getattr(library_manager, "asset_operation_service", None)
            if (
                candidate is not None
                and not self._is_unconfigured_mock(candidate)
                and callable(getattr(candidate, "plan_move_request", None))
            ):
                return candidate

        library_root = library_manager.root() if library_manager is not None else None
        if self._is_unconfigured_mock(library_root):
            library_root = None
        lifecycle_service = (
            getattr(library_manager, "asset_lifecycle_service", None)
            if library_manager is not None
            else None
        )
        if self._is_unconfigured_mock(lifecycle_service):
            lifecycle_service = None
        return create_compat_asset_operation_service(
            library_root,
            lifecycle_service=lifecycle_service,
        )

    def _deleted_directory(
        self,
        library_manager: Optional["LibraryManager"],
    ) -> Path | None:
        if library_manager is None:
            return None
        deleted_directory = getattr(library_manager, "deleted_directory", None)
        if not callable(deleted_directory):
            return None
        try:
            candidate = deleted_directory()
        except Exception:
            return None
        if candidate is None or self._is_unconfigured_mock(candidate):
            return None
        try:
            return Path(candidate)
        except TypeError:
            return None

    @staticmethod
    def _is_unconfigured_mock(candidate: object) -> bool:
        return candidate.__class__.__module__.startswith("unittest.mock")

    def _handle_move_finished(
        self,
        source_root: Path,
        destination_root: Path,
        moved: Sequence[Sequence[Path]],
        source_ok: bool,
        destination_ok: bool,
        worker: MoveWorker,
    ) -> None:
        """Process worker completion and emit appropriate signals."""

        moved_pairs = [(Path(src), Path(dst)) for src, dst in moved]

        if worker.cancelled:
            self.moveFinished.emit(
                source_root,
                destination_root,
                False,
                "Move cancelled.",
            )
            return

        success = bool(moved_pairs) and source_ok and destination_ok

        # Surface the rich completion payload so listeners can distinguish
        # between deletes, restores, and plain moves without replicating the
        # worker bookkeeping logic in multiple layers.
        self.moveCompletedDetailed.emit(
            source_root,
            destination_root,
            moved_pairs,
            source_ok,
            destination_ok,
            worker.is_trash_destination,
            worker.is_restore_operation,
        )

        delete_operation = worker.is_trash_destination and not worker.is_restore_operation
        restore_operation = worker.is_restore_operation
        if not moved_pairs:
            if delete_operation:
                message = "No items were deleted."
            elif restore_operation:
                # Returning an empty string prevents the status bar from
                # showing the standard restore completion toast when the user
                # declined the fallback or when no files could be restored.
                # The controller clears the transient text in this scenario so
                # the previous progress copy does not linger.
                message = ""
            else:
                message = "No files were moved."
        else:
            label = "item" if len(moved_pairs) == 1 else "items"
            if restore_operation:
                verb = "Restored"
                if source_ok and destination_ok:
                    message = f"{verb} {len(moved_pairs)} {label}."
                elif source_ok and not destination_ok:
                    message = (
                        f"{verb} {len(moved_pairs)} {label}, but updating the destination album failed."
                    )
                elif destination_ok and not source_ok:
                    message = (
                        f"{verb} {len(moved_pairs)} {label}, but updating Recently Deleted failed."
                    )
                else:
                    message = (
                        f"{verb} {len(moved_pairs)} {label}, but updating Recently Deleted "
                        "and the destination album failed."
                    )
            else:
                verb = "Deleted" if delete_operation else "Moved"
                if source_ok and destination_ok:
                    message = f"{verb} {len(moved_pairs)} {label}."
                elif delete_operation:
                    if source_ok and not destination_ok:
                        message = (
                            f"{verb} {len(moved_pairs)} {label}, but updating Recently Deleted failed."
                        )
                    elif destination_ok and not source_ok:
                        message = (
                            f"{verb} {len(moved_pairs)} {label}, but updating the original album failed."
                        )
                    else:
                        message = (
                            f"{verb} {len(moved_pairs)} {label}, but updating the original album "
                            "and Recently Deleted failed."
                        )
                elif source_ok or destination_ok:
                    message = (
                        f"{verb} {len(moved_pairs)} {label}, but refreshing one album failed."
                    )
                else:
                    message = (
                        f"{verb} {len(moved_pairs)} {label}, but refreshing both albums failed."
                    )

        self.moveFinished.emit(source_root, destination_root, success, message)

    @Slot(Path, Path)
    def _on_move_started(self, source: Path, destination: Path) -> None:
        """Emit :attr:`moveStarted` while complying with Nuitka's slot validation."""

        self.moveStarted.emit(source, destination)

    @Slot(Path, int, int)
    def _on_move_progress(self, root: Path, current: int, total: int) -> None:
        """Emit :attr:`moveProgress` for worker updates via a dedicated slot."""

        self.moveProgress.emit(root, current, total)

    @Slot(str)
    def _handle_worker_error(self, message: str) -> None:
        """Relay worker errors while keeping Nuitka satisfied with the slot type.

        Nuitka validates the callable passed to :func:`Signal.connect` eagerly and
        refuses method descriptors such as :py:meth:`Signal.emit`.  Routing the
        signal through a dedicated slot preserves the original behaviour—
        forwarding the text message through :attr:`errorRaised`—without
        triggering ``SystemError`` during compilation.
        """

        self.errorRaised.emit(message)


__all__ = ["AssetMoveService"]
