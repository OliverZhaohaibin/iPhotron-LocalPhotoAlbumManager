"""Trash service.

Owns the business rules for the "Recently Deleted" / trash album:
- Initialising and locating the deleted-items directory.
- Computing the library-relative path of the trash album.
- Restore-target album lookup rules.

Extracted from ``TrashManagerMixin`` in ``library/trash_manager.py``.
``LibraryManager`` delegates here while keeping its public API stable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ...config import RECENTLY_DELETED_DIR_NAME
from ...utils.logging import get_logger

LOGGER = get_logger()


class TrashService:
    """Business rules for the library trash / recently-deleted album."""

    # ------------------------------------------------------------------
    # Path computation
    # ------------------------------------------------------------------

    def deleted_dir_path(self, library_root: Path) -> Path:
        """Return the expected path for the trash album under *library_root*."""
        return library_root / RECENTLY_DELETED_DIR_NAME

    def relative_deleted_album_path(
        self,
        trash_root: Path,
        library_root: Path,
    ) -> Optional[str]:
        """Return the POSIX path of the trash album relative to *library_root*.

        Returns ``None`` when the path cannot be resolved.
        """
        try:
            return trash_root.resolve().relative_to(library_root.resolve()).as_posix()
        except (OSError, ValueError):
            pass
        try:
            return trash_root.relative_to(library_root).as_posix()
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Restore rules
    # ------------------------------------------------------------------

    def is_trash_root(self, path: Path, library_root: Optional[Path]) -> bool:
        """Return ``True`` when *path* is the library's trash directory."""
        if library_root is None:
            return path.name == RECENTLY_DELETED_DIR_NAME
        expected = self.deleted_dir_path(library_root)
        try:
            return path.resolve() == expected.resolve()
        except OSError:
            return path == expected

    def restore_origin_is_in_library(
        self,
        destination: Path,
        library_root: Optional[Path],
    ) -> bool:
        """Return ``True`` when *destination* is inside *library_root*.

        Used to decide whether a restore target warrants a rescan.
        """
        if library_root is None:
            return False
        try:
            destination.resolve().relative_to(library_root.resolve())
            return True
        except (ValueError, OSError):
            return False


    def compute_restore_reload_action(
        self,
        restored_path: Path,
        current_root: Optional[Path],
        library_root: Optional[Path],
    ) -> tuple:
        """Determine what UI reload action is needed after a restore rescan.

        Returns a ``(should_reload_current, should_reload_as_library, force_reload)``
        tuple:

        * ``should_reload_current`` – ``True`` when *restored_path* is the
          currently viewed album root and the view should reload.
        * ``should_reload_as_library`` – ``True`` when the current view is the
          library root and *restored_path* is a descendant, so the library view
          should reload.
        * ``force_reload`` – always ``False`` here; the caller may override it
          by consuming a forced-reload marker before emitting the signal.
        """
        if current_root is None:
            return False, False, False

        try:
            path_norm = restored_path.resolve()
        except OSError:
            path_norm = restored_path

        try:
            current_norm = current_root.resolve()
        except OSError:
            current_norm = current_root

        # Case 1: the restored album is the currently open album.
        if path_norm == current_norm:
            return True, False, False

        # Case 2: the current view is the library root and the restored path
        # is a descendant of it.
        if library_root is not None:
            try:
                lib_norm = library_root.resolve()
            except OSError:
                lib_norm = library_root

            if current_norm == lib_norm and self.restore_origin_is_in_library(
                restored_path, library_root
            ):
                return False, True, False

        return False, False, False


__all__ = ["TrashService"]
