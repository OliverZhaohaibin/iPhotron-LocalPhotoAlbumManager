"""Service handling asset deletion on behalf of the facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Set, TYPE_CHECKING

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

        library = self._library_manager_getter()
        if library is None:
            self.errorRaised.emit("Basic Library has not been configured.")
            return False

        try:
            deleted_root = library.ensure_deleted_directory()
        except AlbumOperationError as exc:
            self.errorRaised.emit(str(exc))
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
            normalized.append(candidate)

        if not normalized:
            return False

        # Use model provider to get live motion
        model_provider = self._model_provider_getter()
        model = model_provider() if model_provider else None

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

        return bool(
            self._move_service.move_assets(
                normalized,
                deleted_root,
                operation="delete",
            )
        )


__all__ = ["DeletionService"]
