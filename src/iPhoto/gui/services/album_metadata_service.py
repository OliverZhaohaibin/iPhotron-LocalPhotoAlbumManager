"""Presentation adapter for album manifest mutations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from ...bootstrap.library_album_metadata_service import LibraryAlbumMetadataService
from .session_service_resolver import bound_album_metadata_service

if TYPE_CHECKING:
    from ...library.runtime_controller import LibraryRuntimeController
    from ...application.services.album_manifest_service import Album


class AlbumMetadataService(QObject):
    """Delegate album metadata mutations to the active session surface."""

    errorRaised = Signal(str)

    def __init__(
        self,
        *,
        current_album_getter: Callable[[], "Album | None"],
        library_manager_getter: Callable[[], "LibraryRuntimeController | None"],
        refresh_view: Callable[[Path], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter
        self._refresh_view = refresh_view

    def set_album_cover(
        self,
        album: "Album",
        rel: str,
    ) -> bool:
        if not rel:
            return False

        try:
            self._run_with_watcher_pause(
                lambda service: service.set_cover(album.root, rel),
            )
        except Exception as exc:
            self.errorRaised.emit(str(exc))
            return False

        album.set_cover(rel)
        self._refresh_view(album.root)
        return True

    def toggle_featured(self, album: "Album", ref: str) -> bool:
        if not ref:
            return False

        original_state = ref in album.manifest.get("featured", [])
        try:
            result = self._run_with_watcher_pause(
                lambda service: service.toggle_featured(album.root, ref),
            )
        except Exception as exc:
            self.errorRaised.emit(str(exc))
            return original_state

        next_state = result.is_featured
        for message in result.errors:
            self.errorRaised.emit(message)
        if next_state:
            album.add_featured(ref)
        else:
            album.remove_featured(ref)
        return next_state

    def ensure_featured_entries(
        self,
        root: Path,
        imported: Sequence[Path],
    ) -> None:
        if not imported:
            return

        try:
            self._run_with_watcher_pause(
                lambda service: service.ensure_featured_entries(root, imported),
            )
        except Exception as exc:
            self.errorRaised.emit(str(exc))
            return

        current_album = self._current_album_getter()
        if current_album is None or not self._paths_equal(current_album.root, root):
            return
        for path in imported:
            try:
                rel = Path(path).relative_to(root).as_posix()
            except ValueError:
                continue
            current_album.add_featured(rel)

    def _metadata_service(
        self,
        library_manager: "LibraryRuntimeController | None",
    ) -> LibraryAlbumMetadataService:
        if library_manager is not None:
            library_root = library_manager.root()
        else:
            library_root = None
        if self._is_unconfigured_mock(library_root):
            library_root = None

        if library_root is not None:
            candidate = bound_album_metadata_service(
                library_manager,
                library_root=library_root,
            )
            if candidate is not None:
                return candidate

        raise RuntimeError(
            "Active library session is unavailable; album metadata writes require "
            "a bound LibrarySession."
        )

    def _run_with_watcher_pause(
        self,
        callback: Callable[[LibraryAlbumMetadataService], object],
    ) -> object:
        manager = self._library_manager_getter()
        pause_watcher = getattr(manager, "pause_watcher", None)
        resume_watcher = getattr(manager, "resume_watcher", None)
        if callable(pause_watcher):
            pause_watcher()
        try:
            service = self._metadata_service(manager)
            return callback(service)
        finally:
            if callable(resume_watcher):
                QTimer.singleShot(250, resume_watcher)

    @staticmethod
    def _is_unconfigured_mock(candidate: object) -> bool:
        return candidate.__class__.__module__.startswith("unittest.mock")

    @staticmethod
    def _paths_equal(left: Path, right: Path) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except OSError:
            return Path(left) == Path(right)


__all__ = ["AlbumMetadataService"]
