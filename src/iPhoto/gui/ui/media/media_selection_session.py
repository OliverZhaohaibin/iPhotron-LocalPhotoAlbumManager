"""Pure Python current-media selection session."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol

from .media_restore_request import MediaRestoreRequest
from iPhoto.gui.viewmodels.signal import Signal


class _CollectionReader(Protocol):
    data_changed: Signal

    def count(self) -> int: ...
    def asset_at(self, row: int): ...
    def row_for_path(self, path: Path) -> int | None: ...


class MediaSelectionSession:
    """Own the current selected media across detail-related UI."""

    def __init__(self) -> None:
        self.currentChanged = Signal()
        self.restoreRequested = Signal()

        self._collection: _CollectionReader | None = None
        self._current_row = -1
        self._current_source: Optional[Path] = None

    def bind_collection(self, store_or_reader: _CollectionReader) -> None:
        if self._collection is store_or_reader:
            return
        if self._collection is not None:
            try:
                self._collection.data_changed.disconnect(self._handle_collection_changed)
            except ValueError:
                pass
        self._collection = store_or_reader
        self._collection.data_changed.connect(self._handle_collection_changed)
        self._handle_collection_changed()

    def set_current_row(self, row: int) -> Optional[Path]:
        if self._collection is None:
            return None
        if row < 0 or row >= self._collection.count():
            return None
        dto = self._asset_at(row)
        if dto is None:
            return None
        self._current_row = row
        self._current_source = dto.abs_path
        self.currentChanged.emit(row, dto.abs_path)
        return dto.abs_path

    def set_current_by_path(self, path: Path) -> bool:
        if self._collection is None:
            return False
        row = self._collection.row_for_path(path)
        if row is None:
            return False
        return self.set_current_row(row) is not None

    def current_row(self) -> int:
        return self._current_row

    def current_source(self) -> Optional[Path]:
        return self._current_source

    def request_restore(self, request: MediaRestoreRequest) -> None:
        self.restoreRequested.emit(request)

    def next_row(self) -> Optional[int]:
        if self._collection is None:
            return None
        if self._current_row < 0:
            return 0 if self._collection.count() > 0 else None
        next_row = self._current_row + 1
        if next_row >= self._collection.count():
            return None
        return next_row

    def previous_row(self) -> Optional[int]:
        if self._collection is None:
            return None
        if self._current_row <= 0:
            return None
        return self._current_row - 1

    def _handle_collection_changed(self) -> None:
        if self._collection is None:
            self._current_row = -1
            self._current_source = None
            self.currentChanged.emit(-1, None)
            return
        if self._current_source is not None:
            row = self._collection.row_for_path(self._current_source)
            if row is not None:
                self._current_row = row
                self.currentChanged.emit(row, self._current_source)
                return
        count = self._collection.count()
        if count <= 0:
            self._current_row = -1
            self._current_source = None
            self.currentChanged.emit(-1, None)
            return
        fallback_row = min(max(self._current_row, 0), count - 1)
        dto = self._asset_at(fallback_row)
        if dto is None:
            self._current_row = -1
            self._current_source = None
            self.currentChanged.emit(-1, None)
            return
        self._current_row = fallback_row
        self._current_source = dto.abs_path
        self.currentChanged.emit(fallback_row, dto.abs_path)

    def _asset_at(self, row: int):
        if self._collection is None:
            return None
        asset_at_sync = getattr(self._collection, "asset_at_sync", None)
        if callable(asset_at_sync):
            return asset_at_sync(row)
        return self._collection.asset_at(row)
