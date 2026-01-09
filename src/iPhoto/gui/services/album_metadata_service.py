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
    from ..ui.models.asset_list.model import AssetListModel


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
        """Toggle *ref* inside *album* and mirror updates to other affected albums."""

        if not ref:
            return False

        featured = album.manifest.setdefault("featured", [])
        was_featured = ref in featured
        desired_state = not was_featured

        # Collect all albums that need to be updated:
        # 1. The current album (where the user clicked).
        # 2. The physical album where the file actually resides (if different).
        # 3. The library root album (if different).

        targets: list[tuple[Album, str]] = [(album, ref)]

        library_root: Path | None = None
        manager = self._library_manager_getter()
        if manager is not None:
            library_root = manager.root()

        try:
            absolute_asset = (album.root / ref).resolve()
        except OSError:
            return False

        if library_root:
            # Iterate through all containing albums (Physical Child, Parent, Library Root)
            known_roots = {library_root, album.root}
            containing_roots = self._find_all_containing_albums(
                library_root, absolute_asset, known_roots
            )
            for root in containing_roots:
                if root != album.root:  # Deduplication handled later but good optimization
                    try:
                        alb = Album.open(root)
                        rel = absolute_asset.relative_to(root).as_posix()
                        targets.append((alb, rel))
                    except (OSError, ValueError, IPhotoError) as exc:
                        # Log but continue to update others
                        self.errorRaised.emit(str(exc))

        # Deduplicate targets by album root
        unique_targets: dict[Path, tuple[Album, str]] = {}
        for alb, r in targets:
            unique_targets[alb.root] = (alb, r)

        primary_success = False

        # Process each album atomically: Update Memory -> Persist -> Update DB
        library_root = None
        lib_manager = self._library_manager_getter()
        if lib_manager:
            library_root = lib_manager.root()

        for alb, r in unique_targets.values():
            # Apply changes in memory
            if desired_state:
                alb.add_featured(r)
            else:
                alb.remove_featured(r)

            # Persist changes
            if self._save_manifest(alb, reload_view=False):
                # Success: Update DB immediately to minimize inconsistency window
                # Use library root for global database
                index_root = library_root if library_root else alb.root
                # For global DB, we need the library-relative path
                if library_root:
                    try:
                        lib_rel = absolute_asset.relative_to(library_root).as_posix()
                        IndexStore(index_root).set_favorite_status(lib_rel, desired_state)
                    except (ValueError, OSError):
                        IndexStore(alb.root).set_favorite_status(r, desired_state)
                else:
                    IndexStore(index_root).set_favorite_status(r, desired_state)

                # Check if this was the primary album
                if alb.root == album.root:
                    primary_success = True
            else:
                # Failure: Rollback in-memory state to match disk
                if desired_state:
                    alb.remove_featured(r)
                else:
                    alb.add_featured(r)

        if primary_success:
            self._asset_list_model_provider().update_featured_status(ref, desired_state)
            return desired_state

        return was_featured

    def _find_all_containing_albums(
        self,
        library_root: Path,
        asset_path: Path,
        known_roots: set[Path] | None = None,
    ) -> list[Path]:
        """Traverse upwards to find all physical albums containing the asset."""
        found: list[Path] = []
        candidate = asset_path.parent
        known = known_roots or set()

        while True:
            # Check if candidate is strictly under or equal to library_root
            if candidate != library_root and not is_descendant_path(
                candidate, library_root
            ):
                break

            is_album = False
            # Optimization: Skip I/O if we know this path is an album
            if candidate in known:
                is_album = True
            else:
                # Check if any known manifest file exists
                for name in ALBUM_MANIFEST_NAMES:
                    if (candidate / name).exists():
                        is_album = True
                        break

            if is_album:
                found.append(candidate)

            if candidate == library_root:
                break

            parent = candidate.parent
            if parent == candidate:  # File system root
                break
            candidate = parent

        return found

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
