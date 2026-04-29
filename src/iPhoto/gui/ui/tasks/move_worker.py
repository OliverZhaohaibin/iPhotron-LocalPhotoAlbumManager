"""Worker that moves assets between albums on a background thread."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, Signal

from ....bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService
from ....utils.logging import get_logger

LOGGER = get_logger()


class MoveSignals(QObject):
    """Qt signal bundle used by :class:`MoveWorker` to report progress."""

    started = Signal(Path, Path)
    progress = Signal(Path, int, int)
    # NOTE: Qt's meta-object system cannot parse typing information such as ``list[Path]``
    # when compiling the signal signature. Using the bare ``list`` type keeps the
    # signature compatible across PySide6 versions while still conveying that a Python
    # list containing :class:`pathlib.Path` objects will be emitted.
    # ``finished`` now emits the source root, destination root, a list of
    # ``(original, target)`` path tuples, and two booleans indicating whether the
    # on-disk caches were updated successfully for the respective albums.
    finished = Signal(Path, Path, list, bool, bool)
    error = Signal(str)


class MoveWorker(QRunnable):
    """Move media files to a different album and refresh index caches."""

    def __init__(
        self,
        sources: Iterable[Path],
        source_root: Path,
        destination_root: Path,
        signals: MoveSignals,
        *,
        library_root: Optional[Path] = None,
        trash_root: Optional[Path] = None,
        is_restore: bool = False,
        asset_lifecycle_service: LibraryAssetLifecycleService | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._sources = [Path(path) for path in sources]
        self._source_root = Path(source_root)
        self._destination_root = Path(destination_root)
        self._signals = signals
        self._cancel_requested = False
        self._library_root = self._resolve_optional(library_root)
        self._trash_root = self._resolve_optional(trash_root)
        self._destination_resolved = self._resolve_optional(self._destination_root)
        self._asset_lifecycle_service = asset_lifecycle_service
        # ``_is_restore`` distinguishes restore workflows (moving files out of the
        # trash) from ordinary moves and deletions.  The worker needs this flag to
        # avoid annotating the destination index with ``original_rel_path`` during
        # restore operations because the receiving album should keep its standard
        # schema.
        self._is_restore = bool(is_restore)
        self._is_trash_destination = bool(
            self._destination_resolved
            and self._trash_root
            and self._destination_resolved == self._trash_root
        )

    @property
    def signals(self) -> MoveSignals:
        """Expose the signal container to callers."""

        return self._signals

    @property
    def is_trash_destination(self) -> bool:
        """Return ``True`` when files are being moved into the trash folder."""

        # ``_is_trash_destination`` is computed during initialisation so repeated lookups
        # do not require resolving the paths again.  The facade uses this property to
        # adjust user-facing status messages ("Delete" vs. "Move").
        return self._is_trash_destination

    @property
    def is_restore_operation(self) -> bool:
        """Return ``True`` when the worker is performing a restore from trash."""

        return self._is_restore

    def cancel(self) -> None:
        """Request cancellation of the move operation."""

        self._cancel_requested = True

    @property
    def cancelled(self) -> bool:
        """Return ``True`` when the worker was asked to stop early."""

        return self._cancel_requested

    def run(self) -> None:  # pragma: no cover - executed on a worker thread
        """Move the queued files while updating progress and rescanning albums."""

        total = len(self._sources)
        self._signals.started.emit(self._source_root, self._destination_root)
        if total == 0:
            self._signals.finished.emit(
                self._source_root,
                self._destination_root,
                [],
                True,
                True,
            )
            return

        moved: List[Tuple[Path, Path]] = []
        for index, source in enumerate(self._sources, start=1):
            if self._cancel_requested:
                break
            try:
                try:
                    source_path = source.resolve()
                except OSError:
                    source_path = source
                target = self._move_into_destination(source_path)
            except FileNotFoundError:
                self._signals.error.emit(f"File not found: {source}")
            except OSError as exc:
                self._signals.error.emit(f"Could not move '{source}': {exc}")
            else:
                moved.append((source_path, target))
            finally:
                self._signals.progress.emit(self._source_root, index, total)

        source_index_ok = True
        destination_index_ok = True
        if moved and not self._cancel_requested:
            result = self.asset_lifecycle_service.apply_move(
                moved=moved,
                source_root=self._source_root,
                destination_root=self._destination_root,
                trash_root=self._trash_root,
                is_restore=self._is_restore,
            )
            source_index_ok = result.source_index_ok
            destination_index_ok = result.destination_index_ok
            for message in result.errors:
                self._signals.error.emit(message)

        self._signals.finished.emit(
            self._source_root,
            self._destination_root,
            moved,
            source_index_ok,
            destination_index_ok,
        )

    def _move_into_destination(self, source: Path) -> Path:
        """Move *source* into the destination album avoiding name collisions."""

        if not source.exists():
            raise FileNotFoundError(source)
        target_dir = self._destination_root
        base_name = source.name
        target = target_dir / base_name
        stem = target.stem
        suffix = target.suffix
        counter = 1
        while target.exists():
            target = target_dir / f"{stem} ({counter}){suffix}"
            counter += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            moved_path = shutil.move(str(source), str(target))
        except OSError:
            if source.exists() and target.exists():
                try:
                    target.unlink()
                except OSError:
                    LOGGER.warning("Failed to clean up partially moved target %s", target, exc_info=True)
            raise
        return Path(moved_path).resolve()

    @property
    def asset_lifecycle_service(self) -> LibraryAssetLifecycleService:
        """Return the session lifecycle service, creating a compatibility fallback."""

        if self._asset_lifecycle_service is None:
            self._asset_lifecycle_service = LibraryAssetLifecycleService(
                self._library_root,
            )
        return self._asset_lifecycle_service

    def _resolve_optional(self, path: Optional[Path]) -> Optional[Path]:
        """Resolve *path* defensively, returning ``None`` when unavailable."""

        if path is None:
            return None
        try:
            return path.resolve()
        except OSError:
            return path


__all__ = ["MoveSignals", "MoveWorker"]
