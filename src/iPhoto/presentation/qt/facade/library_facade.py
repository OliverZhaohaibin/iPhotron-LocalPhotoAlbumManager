"""Asset-oriented operations extracted from the monolithic AppFacade.

Responsibilities:
- import_files
- move_assets
- delete_assets
- restore_assets
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....gui.services import (
        AssetImportService,
        AssetMoveService,
        DeletionService,
        RestorationService,
    )


class AssetFacade:
    """Encapsulates asset import, move, delete and restore operations."""

    def __init__(
        self,
        *,
        import_service: "AssetImportService",
        move_service: "AssetMoveService",
        deletion_service: "DeletionService",
        restoration_service: "RestorationService",
    ) -> None:
        self._import_service = import_service
        self._move_service = move_service
        self._deletion_service = deletion_service
        self._restoration_service = restoration_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
