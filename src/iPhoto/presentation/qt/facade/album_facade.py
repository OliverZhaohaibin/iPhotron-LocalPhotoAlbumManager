"""Album-oriented operations extracted from the monolithic AppFacade.

Responsibilities:
- open_album
- set_cover
- toggle_featured
- pair_live_current
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ....errors import IPhotoError

if TYPE_CHECKING:  # pragma: no cover
    from ....gui.services.album_metadata_service import AlbumMetadataService
    from ....gui.services.library_update_service import LibraryUpdateService
    from ....library.manager import LibraryManager
    from ....models.album import Album

_logger = logging.getLogger(__name__)


class AlbumFacade:
    """Encapsulates album lifecycle and metadata write operations."""

    def __init__(
        self,
        *,
        backend_bridge,
        metadata_service: AlbumMetadataService,
        library_update_service: LibraryUpdateService,
        current_album_getter: Callable[[], Album | None],
        current_album_setter: Callable[[Album | None], None],
        library_manager_getter: Callable[[], LibraryManager | None],
        error_emitter: Callable[[str], None],
        album_opened_emitter: Callable[[Path], None],
        load_started_emitter: Callable[[Path], None],
        load_finished_emitter: Callable[[Path, bool], None],
        rescan_trigger: Callable[[], None],
    ) -> None:
        self._backend = backend_bridge
        self._metadata_service = metadata_service
        self._library_update_service = library_update_service
        self._current_album_getter = current_album_getter
        self._current_album_setter = current_album_setter
        self._library_manager_getter = library_manager_getter
        self._error = error_emitter
        self._album_opened = album_opened_emitter
        self._load_started = load_started_emitter
        self._load_finished = load_finished_emitter
        self._rescan_trigger = rescan_trigger

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_album(self, root: Path) -> Album | None:
        """Open *root* and trigger background work as needed."""

        from ....cache.index_store import get_global_repository

        library_manager = self._library_manager_getter()
        library_root = library_manager.root() if library_manager else None

        try:
            album = self._backend.open_album(
                root,
                autoscan=False,
                library_root=library_root,
                hydrate_index=False,
            )
        except IPhotoError as exc:
            self._error(str(exc))
            return None

        self._current_album_setter(album)
        album_root = album.root
        self._album_opened(album_root)

        # Check if the index is empty and trigger a background scan if so.
        index_root = library_root if library_root else album_root
        has_assets = False
        try:
            store = get_global_repository(index_root)
            next(store.read_all())
            has_assets = True
        except (StopIteration, IPhotoError):
            pass

        is_already_scanning = (
            library_manager is not None and library_manager.is_scanning_path(album_root)
        )
        if not has_assets and not is_already_scanning:
            self._rescan_trigger()

        self._load_started(album_root)
        self._load_finished(album_root, True)
        return album

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

    def pair_live_current(self) -> list[dict]:
        """Rebuild Live Photo pairings for the active album."""

        album = self._current_album_getter()
        if album is None:
            self._error("No album is currently open.")
            return []
        return self._library_update_service.pair_live(album)
