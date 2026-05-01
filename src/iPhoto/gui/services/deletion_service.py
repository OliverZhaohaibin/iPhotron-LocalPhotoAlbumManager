"""Service handling asset deletion on behalf of the facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from ...errors import AlbumOperationError

if TYPE_CHECKING:
    from ...library.manager import LibraryManager
    from .asset_move_service import AssetMoveService


class DeletionService(QObject):
    """Move selected assets into the dedicated deleted-items folder."""

    errorRaised = Signal(str)

    def __init__(
        self,
        *,
        move_service: "AssetMoveService",
        library_manager_getter: Callable[[], Optional["LibraryManager"]],
        model_provider_getter: Callable[[], Optional[Callable[[], Any]]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._move_service = move_service
        self._library_manager_getter = library_manager_getter
        self._model_provider_getter = model_provider_getter

    def delete_assets(self, sources: Iterable[Path]) -> bool:
        """Move *sources* into the dedicated deleted-items folder."""

        requested_sources = list(sources)
        if not requested_sources:
            return False

        library = self._library_manager_getter()
        if library is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return False

        try:
            deleted_root = library.ensure_deleted_directory()
        except AlbumOperationError as exc:
            self.errorRaised.emit(str(exc))
            return False

        model_provider = self._model_provider_getter()
        model = model_provider() if model_provider else None

        def _metadata_lookup(path: Path):
            if model is None or not hasattr(model, "metadata_for_path"):
                return None
            return model.metadata_for_path(path)

        return bool(
            self._move_service.move_assets(
                requested_sources,
                deleted_root,
                operation="delete",
                metadata_lookup=_metadata_lookup,
            )
        )


__all__ = ["DeletionService"]
