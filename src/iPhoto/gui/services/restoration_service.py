"""Service handling asset restoration from the deleted-items folder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from ...bootstrap.library_asset_operation_service import (
    AssetMovePlan,
    LibraryAssetOperationService,
)
from .session_service_resolver import bound_asset_operation_service

if TYPE_CHECKING:
    from ...library.runtime_controller import LibraryRuntimeController
    from .asset_move_service import AssetMoveService


@dataclass(frozen=True)
class RestoreBatch:
    """One submitted restore group with its resolved destination album."""

    sources: list[Path]
    destination_root: Path


@dataclass(frozen=True)
class RestoreScheduleResult:
    """Result returned after restore planning and task submission."""

    batches: list[RestoreBatch] = field(default_factory=list)

    @property
    def scheduled(self) -> bool:
        return bool(self.batches)


class RestorationService(QObject):
    """Return trashed assets to their original album locations."""

    errorRaised = Signal(str)

    def __init__(
        self,
        *,
        move_service: "AssetMoveService",
        library_manager_getter: Callable[[], Optional["LibraryRuntimeController"]],
        model_provider_getter: Callable[[], Optional[Callable[[], Any]]],
        restore_prompt_getter: Callable[[], Optional[Callable[[str], bool]]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._move_service = move_service
        self._library_manager_getter = library_manager_getter
        self._model_provider_getter = model_provider_getter
        self._restore_prompt_getter = restore_prompt_getter

    def restore_assets(self, sources: Iterable[Path]) -> bool:
        """Return ``True`` when at least one trashed asset restore is scheduled."""

        return self.restore_assets_with_plan(sources).scheduled

    def restore_assets_with_plan(self, sources: Iterable[Path]) -> RestoreScheduleResult:
        """Schedule restores and return the accepted source/destination groups."""

        requested_sources = list(sources)
        if not requested_sources:
            return RestoreScheduleResult()

        library = self._library_manager_getter()
        if library is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return RestoreScheduleResult()

        library_root = library.root()
        if library_root is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return RestoreScheduleResult()

        trash_root = library.deleted_directory()
        if trash_root is None:
            self.errorRaised.emit("Recently Deleted folder is unavailable.")
            return RestoreScheduleResult()

        model_provider = self._model_provider_getter()
        model = model_provider() if model_provider else None

        def _metadata_lookup(path: Path):
            if model is None or not hasattr(model, "metadata_for_path"):
                return None
            return model.metadata_for_path(path)

        try:
            operation_service = self._operation_service(library, library_root)
        except RuntimeError as exc:
            self.errorRaised.emit(str(exc))
            return RestoreScheduleResult()
        restore_plan = operation_service.plan_restore_request(
            requested_sources,
            trash_root=trash_root,
            metadata_lookup=_metadata_lookup,
            restore_to_root_prompt=self._restore_prompt_getter(),
        )

        for message in restore_plan.errors:
            self.errorRaised.emit(message)

        submitted_batches: list[RestoreBatch] = []
        for batch in restore_plan.batches:
            if batch.destination_root is None:
                continue
            if self._submit_batch(batch):
                submitted_batches.append(
                    RestoreBatch(
                        sources=list(batch.sources),
                        destination_root=Path(batch.destination_root),
                    )
                )

        return RestoreScheduleResult(batches=submitted_batches)

    def _submit_batch(self, batch: AssetMovePlan) -> bool:
        submit_plan = getattr(self._move_service, "submit_plan", None)
        if callable(submit_plan):
            return bool(submit_plan(batch))
        if batch.destination_root is None:
            return False
        return bool(
            self._move_service.move_assets(
                batch.sources,
                batch.destination_root,
                operation="restore",
            )
        )

    def _operation_service(
        self,
        library: "LibraryRuntimeController",
        library_root: Path,
    ) -> LibraryAssetOperationService:
        candidate = bound_asset_operation_service(
            library,
            library_root=library_root,
        )
        if candidate is not None:
            return candidate

        raise RuntimeError(
            "Active library session is unavailable; restore operations require a "
            "bound LibrarySession."
        )

    @staticmethod
    def _is_unconfigured_mock(candidate: object) -> bool:
        return candidate.__class__.__module__.startswith("unittest.mock")


__all__ = ["RestorationService", "RestoreBatch", "RestoreScheduleResult"]
