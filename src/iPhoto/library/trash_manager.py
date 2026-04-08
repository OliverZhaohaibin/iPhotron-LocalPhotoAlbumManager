"""Trash/deleted items management."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..config import (
    RECENTLY_DELETED_DIR_NAME,
    WORK_DIR_NAME,
)
from ..errors import (
    AlbumOperationError,
    IPhotoError,
)
from ..cache.index_store import get_global_repository
from ..utils.logging import get_logger

if TYPE_CHECKING:
    pass

LOGGER = get_logger()


class TrashManagerMixin:
    """Mixin providing trash/deleted items management for LibraryManager."""

    def ensure_deleted_directory(self) -> Path:
        """Create the dedicated trash directory when missing and return it."""

        root = self._require_root()
        target = root / RECENTLY_DELETED_DIR_NAME
        self._migrate_legacy_deleted_dir(root, target)
        if target.exists() and not target.is_dir():
            raise AlbumOperationError(
                f"Deleted items path exists but is not a directory: {target}"
            )
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AlbumOperationError(
                f"Could not prepare deleted items folder: {exc}"
            ) from exc
        self._deleted_dir = target
        return target

    def deleted_directory(self) -> Path | None:
        """Return the path to the trash directory, creating it on demand."""

        if self._root is None:
            self._deleted_dir = None
            return None
        cached = self._deleted_dir
        if cached is not None and cached.exists():
            return cached
        try:
            return self.ensure_deleted_directory()
        except AlbumOperationError as exc:
            self.errorRaised.emit(str(exc))
            return None

    def cleanup_deleted_index(self) -> int:
        """Drop stale trash entries from the global index.

        This performs a best-effort cleanup of index rows corresponding to items
        in the deleted-items album that no longer exist on disk. Database-related
        errors (for example, ``sqlite3.Error`` or ``IPhotoError`` raised by the
        index store) are caught and suppressed. In such error conditions, the
        method may return ``0`` or remove only a subset of stale entries, so
        callers should not rely on it to guarantee a fully cleaned index.

        Returns the number of rows removed.
        """

        root = self._root
        trash_root = self.deleted_directory()
        if root is None or trash_root is None:
            return 0

        album_path = self._relative_deleted_album_path(trash_root, root)
        if album_path is None:
            return 0

        try:
            has_files = next(trash_root.iterdir(), None) is not None
        except OSError:
            has_files = False

        store = get_global_repository(root)
        try:
            entry_count = store.count(
                album_path=album_path,
                include_subalbums=True,
                filter_hidden=False,
            )
        except (sqlite3.Error, IPhotoError) as exc:
            LOGGER.warning(
                "Failed to count deleted items for album %s during cleanup: %s",
                album_path,
                exc,
            )
            return 0

        if entry_count == 0:
            return 0

        missing: list[str] = []

        def _is_missing(rel: str) -> bool:
            return not has_files or not (root / rel).exists()

        for row in store.read_album_assets(
            album_path,
            include_subalbums=True,
            filter_hidden=False,
        ):
            rel = row.get("rel")
            if not isinstance(rel, str):
                continue
            if _is_missing(rel):
                missing.append(rel)

        if missing:
            store.remove_rows(missing)
        return len(missing)

    def _relative_deleted_album_path(self, trash_root: Path, root: Path) -> Optional[str]:
        """Return the trash path relative to the library root, or ``None``."""

        try:
            return trash_root.resolve().relative_to(root.resolve()).as_posix()
        except OSError:
            pass
        try:
            return trash_root.relative_to(root).as_posix()
        except ValueError:
            return None

    def _initialize_deleted_dir(self) -> None:
        """Prepare the deleted-items directory while swallowing recoverable errors."""

        if self._root is None:
            self._deleted_dir = None
            return
        try:
            self.ensure_deleted_directory()
        except AlbumOperationError as exc:
            # Creation failures are surfaced to the UI while the library remains usable.
            self._deleted_dir = None
            self.errorRaised.emit(str(exc))

    def _migrate_legacy_deleted_dir(self, root: Path, target: Path) -> None:
        """Move data from the legacy ``.iPhoto/deleted`` path into *target*.

        Earlier builds stored trashed assets inside ``.iPhoto/deleted`` which
        made the collection difficult to locate from outside the application.
        When upgrading we want to preserve any existing deletions by moving the
        entire folder into the new root-level trash.  When a plain rename is not
        possible we fall back to copying individual entries while avoiding
        filename collisions.
        """

        legacy = root / WORK_DIR_NAME / "deleted"
        if not legacy.exists() or not legacy.is_dir():
            return

        try:
            if not target.exists():
                legacy.rename(target)
                return
        except OSError as exc:
            raise AlbumOperationError(
                f"Could not migrate legacy deleted folder: {exc}"
            ) from exc

        for entry in legacy.iterdir():
            if entry.name == WORK_DIR_NAME:
                destination_parent = target / WORK_DIR_NAME
                destination_parent.mkdir(parents=True, exist_ok=True)
                for child in entry.iterdir():
                    destination = self._unique_child_path(
                        destination_parent, child.name
                    )
                    try:
                        shutil.move(str(child), str(destination))
                    except OSError as exc:
                        raise AlbumOperationError(
                            f"Could not migrate legacy deleted cache '{child}': {exc}"
                        ) from exc
                continue

            destination = self._unique_child_path(target, entry.name)
            try:
                shutil.move(str(entry), str(destination))
            except OSError as exc:
                raise AlbumOperationError(
                    f"Could not migrate legacy deleted entry '{entry}': {exc}"
                ) from exc

        try:
            legacy.rmdir()
        except OSError:
            # Leaving the empty folder behind is harmless and avoids masking
            # migration successes when the directory still contains temporary
            # files created by external tools.
            pass

    def _unique_child_path(self, parent: Path, name: str) -> Path:
        """Return a path under *parent* that avoids overwriting existing files."""

        candidate = parent / name
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            next_candidate = parent / f"{stem} ({counter}){suffix}"
            if not next_candidate.exists():
                return next_candidate
            counter += 1
