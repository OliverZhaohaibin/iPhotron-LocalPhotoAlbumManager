"""Trash/deleted items management."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import (
    RECENTLY_DELETED_DIR_NAME,
)
from ..errors import (
    AlbumOperationError,
)
from ..utils.pathutils import resolve_work_dir

if TYPE_CHECKING:
    pass


class TrashManagerMixin:
    """Mixin providing trash/deleted items management for LibraryRuntimeController."""

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
        """Drop stale trash entries through the active lifecycle boundary.

        This performs a best-effort cleanup of index rows corresponding to items
        in the deleted-items album that no longer exist on disk. Repository
        errors are handled by the lifecycle service and return ``0``.

        Returns the number of rows removed.
        """

        root = self._root
        trash_root = self.deleted_directory()
        if root is None or trash_root is None:
            return 0

        lifecycle_service = getattr(self, "asset_lifecycle_service", None)
        cleanup_deleted_index = getattr(
            lifecycle_service,
            "cleanup_deleted_index",
            None,
        )
        if not callable(cleanup_deleted_index):
            return 0
        return int(cleanup_deleted_index(trash_root))

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

        work_dir = resolve_work_dir(root)
        if work_dir is None:
            return
        legacy = work_dir / "deleted"
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
            if entry.name.casefold() in {".iphoto"}:
                destination_parent = target / entry.name
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
