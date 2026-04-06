"""Album-oriented operations extracted from the monolithic AppFacade.

Responsibilities:
- open_album
- set_cover
- toggle_featured
- pair_live_current
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ....models.album import Album
    from ....gui.services.library_update_service import LibraryUpdateService
    from ....gui.services.album_metadata_service import AlbumMetadataService
    from ....library.manager import LibraryManager


class AlbumFacade:
    """Encapsulates album lifecycle and metadata write operations."""

    def __init__(
        self,
        *,
        backend_bridge,
        metadata_service: "AlbumMetadataService",
        library_update_service: "LibraryUpdateService",
        current_album_getter: Callable[[], Optional["Album"]],
        library_manager_getter: Callable[[], Optional["LibraryManager"]],
        error_emitter: Callable[[str], None],
        album_opened_emitter: Callable[[Path], None],
        load_started_emitter: Callable[[Path], None],
        load_finished_emitter: Callable[[Path, bool], None],
    ) -> None:
        self._backend = backend_bridge
        self._metadata_service = metadata_service
        self._library_update_service = library_update_service
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter
        self._error = error_emitter
        self._album_opened = album_opened_emitter
        self._load_started = load_started_emitter
        self._load_finished = load_finished_emitter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_album(self, root: Path) -> Optional["Album"]:
        """Delegate to the facade's open_album implementation."""
        # NOTE: The real implementation lives in AppFacade.open_album and
        # delegates here.  This method is the hook point for future migration.
        raise NotImplementedError("Delegate to AppFacade.open_album until migrated")

    def set_cover(self, rel: str) -> bool:
        """Set the album cover to *rel* and persist the manifest."""

        album = self._current_album_getter()
        if album is None:
            self._error("No album is currently open.")
            return False
        return self._metadata_service.set_album_cover(album, rel)

    def toggle_featured(self, ref: str) -> bool:
        """Toggle *ref* in the active album and mirror the change in the library."""

        album = self._current_album_getter()
        if album is None or not ref:
            self._error("No album is currently open.")
            return False
        return self._metadata_service.toggle_featured(album, ref)

    def pair_live_current(self) -> List[dict]:
        """Rebuild Live Photo pairings for the active album."""

        album = self._current_album_getter()
        if album is None:
            self._error("No album is currently open.")
            return []
        return self._library_update_service.pair_live(album)
