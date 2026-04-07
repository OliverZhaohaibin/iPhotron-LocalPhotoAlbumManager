"""Library-oriented operations extracted from the monolithic AppFacade.

Responsibilities:
- rescan_current (sync)
- rescan_current_async
- cancel_active_scans
- announce_album_refresh
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE

if TYPE_CHECKING:  # pragma: no cover
    from ....gui.background_task_manager import BackgroundTaskManager
    from ....gui.services.library_update_service import LibraryUpdateService
    from ....library.manager import LibraryManager
    from ....models.album import Album


class LibraryFacade:
    """Encapsulates library scan, refresh and cancellation operations."""

    def __init__(
        self,
        *,
        library_update_service: LibraryUpdateService,
        task_manager: BackgroundTaskManager,
        current_album_getter: Callable[[], Album | None],
        library_manager_getter: Callable[[], LibraryManager | None],
        error_emitter: Callable[[str], None],
    ) -> None:
        self._library_update_service = library_update_service
        self._task_manager = task_manager
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter
        self._error = error_emitter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def replace_library_update_service(self, service: LibraryUpdateService) -> None:
        """Replace the held update service. Intended for test injection only."""

        self._library_update_service = service

    def rescan_current(self) -> list[dict]:
        """Synchronously rescan the active album."""

        album = self._current_album_getter()
        if album is None:
            self._error("No album is currently open.")
            return []
        return self._library_update_service.rescan_album(album)

    def rescan_current_async(self) -> None:
        """Start a background rescan for the active album."""

        album = self._current_album_getter()
        if album is None:
            self._error("No album is currently open.")
            return

        library_manager = self._library_manager_getter()
        if library_manager is not None:
            filters = album.manifest.get("filters", {}) if isinstance(album.manifest, dict) else {}
            include = filters.get("include", DEFAULT_INCLUDE)
            exclude = filters.get("exclude", DEFAULT_EXCLUDE)
            library_manager.start_scanning(album.root, include, exclude)
        else:
            self._library_update_service.rescan_album_async(album)

    def cancel_active_scans(self) -> None:
        """Request cancellation of any in-flight scan operations."""

        library_manager = self._library_manager_getter()
        if library_manager is not None:
            try:
                library_manager.stop_scanning()
                library_manager.pause_watcher()
            except RuntimeError:
                pass

        self._library_update_service.cancel_active_scan()

    def announce_album_refresh(
        self,
        root: Path,
        *,
        request_reload: bool = True,
        force_reload: bool = False,
        announce_index: bool = False,
    ) -> None:
        """Emit index refresh signals for *root* and optionally request a reload."""

        self._library_update_service.announce_album_refresh(
            root,
            request_reload=request_reload,
            force_reload=force_reload,
            announce_index=announce_index,
        )
