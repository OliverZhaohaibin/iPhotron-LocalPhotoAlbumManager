"""Service that centralises manifest writes for album-related actions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from ...cache.index_store import IndexStore
from ...config import ALBUM_MANIFEST_NAMES
from ...errors import IPhotoError
from ...models.album import Album
from ...utils.pathutils import is_descendant_path

if TYPE_CHECKING:
    from ...library.manager import LibraryManager
    from ..ui.models.asset_list_model import AssetListModel


class AlbumMetadataService(QObject):
    """Persist manifest mutations on behalf of the GUI while shielding the UI."""

    errorRaised = Signal(str)

    def __init__(
        self,
        *,
        asset_list_model_provider: Callable[[], AssetListModel],
        current_album_getter: Callable[[], Album | None],
        library_manager_getter: Callable[[], LibraryManager | None],
        refresh_view: Callable[[Path], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_list_model_provider = asset_list_model_provider
        self._current_album_getter = current_album_getter
        self._library_manager_getter = library_manager_getter
        self._refresh_view = refresh_view

    # ------------------------------------------------------------------
    # Public API used by :class:`~iPhoto.gui.facade.AppFacade`
    # ------------------------------------------------------------------
    def set_album_cover(self, album: Album, rel: str) -> bool:
        """Set *rel* as cover for *album* and persist the change."""

        if not rel:
            return False
        album.set_cover(rel)
        return self._save_manifest(album)

    def toggle_featured(self, album: Album, ref: str) -> bool:
        """Toggle *ref* inside *album* and mirror updates to the library root."""

        if not ref:
            return False

        featured = album.manifest.setdefault("featured", [])
        was_featured = ref in featured
        desired_state = not was_featured

        library_root: Path | None = None

        manager = self._library_manager_getter()
        if manager is not None:
            library_root = manager.root()

        target_album: Album | None = None
        target_ref: str | None = None

        if library_root is not None:
            if library_root != album.root:
                # Case 1: Toggling in a sub-album. Need to update the Library Root as well.
                try:
                    absolute_asset = (album.root / ref).resolve()
                    root_relative = absolute_asset.relative_to(library_root.resolve())
                except (OSError, ValueError):
                    pass
                else:
                    target_ref = root_relative.as_posix()
                    try:
                        target_album = Album.open(library_root)
                    except IPhotoError as exc:
                        self.errorRaised.emit(str(exc))
                        target_album = None
            else:
                # Case 2: Toggling in the Library Root. Need to update the physical sub-album.
                try:
                    absolute_asset = (album.root / ref).resolve()
                    # Instead of assuming the immediate parent is the album, search for the real root.
                    physical_root = self._find_containing_physical_album(
                        library_root, absolute_asset
                    )

                    if physical_root:
                        # Calculate the correct relative path from the actual physical root
                        # e.g., converts absolute path to "SubFolder/Photo.jpg"
                        target_ref = absolute_asset.relative_to(physical_root).as_posix()

                        try:
                            target_album = Album.open(physical_root)
                        except IPhotoError as exc:
                            self.errorRaised.emit(str(exc))
                            target_album = None
                    else:
                        # No physical album found (e.g., file is directly in Library Root or is an orphan).
                        # Skip synchronization.
                        target_album = None

                except (OSError, ValueError) as exc:
                    self.errorRaised.emit(str(exc))

        if desired_state:
            album.add_featured(ref)
            if target_album is not None and target_ref is not None:
                target_album.add_featured(target_ref)
        else:
            album.remove_featured(ref)
            if target_album is not None and target_ref is not None:
                target_album.remove_featured(target_ref)

        current_saved = self._save_manifest(album, reload_view=False)
        target_saved = True
        if target_album is not None and target_ref is not None:
            target_saved = self._save_manifest(target_album, reload_view=False)

        if current_saved and target_saved:
            # Update DB index after successful manifest save.
            # Any transient inconsistency (e.g. DB update failure) is self-corrected
            # by sync_favorites() on the next album load.
            IndexStore(album.root).set_favorite_status(ref, desired_state)
            if target_album is not None and target_ref is not None:
                IndexStore(target_album.root).set_favorite_status(target_ref, desired_state)
            self._asset_list_model_provider().update_featured_status(ref, desired_state)
            return desired_state

        # Persistence failed. Roll back to the previous manifest state so the
        # in-memory representation stays consistent with the on-disk version.
        if desired_state:
            album.remove_featured(ref)
            if target_album is not None and target_ref is not None:
                target_album.remove_featured(target_ref)
        else:
            album.add_featured(ref)
            if target_album is not None and target_ref is not None:
                target_album.add_featured(target_ref)
        return was_featured

    def _find_containing_physical_album(self, library_root: Path, asset_path: Path) -> Path | None:
        """Traverse upwards from the asset path to find the nearest physical album root."""
        candidate = asset_path.parent

        # Safety check: Ensure we stay strictly within the library root
        while candidate != library_root and is_descendant_path(candidate, library_root):
            # Check if any known manifest file exists in the current candidate directory
            for name in ALBUM_MANIFEST_NAMES:
                if (candidate / name).exists():
                    return candidate

            # Stop if we hit the filesystem root to prevent infinite loops
            if candidate.parent == candidate:
                break

            # Move up one level
            candidate = candidate.parent

        return None

    def ensure_featured_entries(
        self,
        root: Path,
        imported: Sequence[Path],
    ) -> None:
        """Flag imported assets under *root* as featured when requested."""

        if not imported:
            return

        album = self._resolve_album_for_root(root)
        if album is None:
            return

        updated = False
        for path in imported:
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            album.add_featured(rel)
            updated = True

        if not updated:
            return

        self._save_manifest(album, reload_view=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_album_for_root(self, root: Path) -> Album | None:
        """Return an :class:`Album` instance representing *root*."""

        current = self._current_album_getter()
        if current is not None and current.root == root:
            return current
        try:
            return Album.open(root)
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            return None

    def _save_manifest(self, album: Album, *, reload_view: bool = True) -> bool:
        """Persist *album* to disk, optionally requesting a UI refresh."""

        manager = self._library_manager_getter()
        if manager is not None:
            manager.pause_watcher()
        try:
            album.save()
        except IPhotoError as exc:
            self.errorRaised.emit(str(exc))
            return False
        finally:
            if manager is not None:
                QTimer.singleShot(250, manager.resume_watcher)

        if reload_view:
            self._refresh_view(album.root)
        return True


__all__ = ["AlbumMetadataService"]
