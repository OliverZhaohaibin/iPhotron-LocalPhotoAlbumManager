"""Pure Python current-media selection session."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol

from iPhoto.gui.viewmodels.signal import Signal

from .media_restore_request import MediaRestoreRequest


class _CollectionReader(Protocol):
    data_changed: Signal

    def count(self) -> int: ...
    def asset_at(self, row: int): ...
    def ensure_row_loaded(self, row: int, *, emit_signals: bool = True) -> bool: ...
    def row_for_path(self, path: Path) -> int | None: ...
    def cached_row_for_path(self, path: Path) -> int | None: ...
    def selection_anchor_status(self, path: Path) -> str | None: ...


class MediaSelectionState(str, Enum):
    """Describe whether the selected asset currently has an authoritative row."""

    NONE = "none"
    RESOLVED = "resolved"
    ANCHOR_RESOLVING = "anchor_resolving"
    FALLBACK_PENDING = "fallback_pending"


class MediaSelectionChangeReason(str, Enum):
    """Describe why one coherent selection snapshot was published."""

    USER_SELECTED = "user_selected"
    ANCHOR_PENDING = "anchor_pending"
    ANCHOR_RESOLVED = "anchor_resolved"
    FALLBACK_PENDING = "fallback_pending"
    FALLBACK_RESOLVED = "fallback_resolved"
    CLEARED = "cleared"


@dataclass(frozen=True, slots=True)
class MediaSelectionSnapshot:
    """Immutable selection state shared by Detail-related consumers.

    ``row`` is authoritative only for :attr:`MediaSelectionState.RESOLVED`.
    Pending snapshots retain the stable asset identity solely so the resident
    presentation can remain visible while Gallery resolves its new position.
    """

    version: int
    state: MediaSelectionState
    row: int | None
    path: Path | None
    asset_id: str | None

    @property
    def is_resolved(self) -> bool:
        return self.state is MediaSelectionState.RESOLVED and self.row is not None


class MediaSelectionSession:
    """Own the current selected media across detail-related UI."""

    def __init__(self) -> None:
        self.selectionChanged = Signal()
        self.navigationRequested = Signal()
        self.restoreRequested = Signal()

        self._collection: _CollectionReader | None = None
        self._snapshot = MediaSelectionSnapshot(
            version=0,
            state=MediaSelectionState.NONE,
            row=None,
            path=None,
            asset_id=None,
        )
        self._last_resolved_row = -1
        self._pending_fallback_row: int | None = None
        self._pending_navigation_delta = 0

    def bind_collection(self, store_or_reader: _CollectionReader) -> None:
        if self._collection is store_or_reader:
            return
        if self._collection is not None:
            try:
                self._collection.data_changed.disconnect(self._handle_collection_changed)
            except ValueError:
                pass
            row_loaded = getattr(self._collection, "row_loaded", None)
            if row_loaded is not None:
                try:
                    row_loaded.disconnect(self._handle_row_loaded)
                except ValueError:
                    pass
        self._collection = store_or_reader
        self._collection.data_changed.connect(self._handle_collection_changed)
        row_loaded = getattr(self._collection, "row_loaded", None)
        if row_loaded is not None:
            row_loaded.connect(self._handle_row_loaded)
        self._handle_collection_changed()

    def set_current_row(self, row: int) -> Optional[Path]:
        if self._collection is None:
            return None
        if row < 0 or row >= self._collection.count():
            return None
        dto = self._collection.asset_at(row)
        if dto is None:
            ensure_row_loaded = getattr(self._collection, "ensure_row_loaded", None)
            if callable(ensure_row_loaded):
                ensure_row_loaded(row)
                dto = self._collection.asset_at(row)
        if dto is None:
            return None
        self._last_resolved_row = row
        self._pending_fallback_row = None
        self._pending_navigation_delta = 0
        pin_path = getattr(self._collection, "pin_path", None)
        if callable(pin_path):
            pin_path(dto.abs_path, asset_id=str(dto.id), previous_row=row)
        self._publish_snapshot(
            state=MediaSelectionState.RESOLVED,
            row=row,
            path=dto.abs_path,
            asset_id=str(dto.id),
            reason=MediaSelectionChangeReason.USER_SELECTED,
            force=True,
        )
        return dto.abs_path

    def set_current_by_path(self, path: Path) -> bool:
        if self._collection is None:
            return False
        row = self._collection.row_for_path(path)
        if row is None:
            return False
        return self.set_current_row(row) is not None

    def selection_snapshot(self) -> MediaSelectionSnapshot:
        return self._snapshot

    def current_row(self) -> int:
        return self._snapshot.row if self._snapshot.row is not None else -1

    def current_source(self) -> Optional[Path]:
        return self._snapshot.path

    def selection_state(self) -> MediaSelectionState:
        return self._snapshot.state

    def request_restore(self, request: MediaRestoreRequest) -> None:
        self.restoreRequested.emit(request)

    def next_row(self) -> Optional[int]:
        if self._collection is None:
            return None
        if self._snapshot.state in {
            MediaSelectionState.ANCHOR_RESOLVING,
            MediaSelectionState.FALLBACK_PENDING,
        }:
            self._pending_navigation_delta += 1
            return None
        current_row = self.current_row()
        if current_row < 0:
            return 0 if self._collection.count() > 0 else None
        next_row = current_row + 1
        if next_row >= self._collection.count():
            return None
        return next_row

    def previous_row(self) -> Optional[int]:
        if self._collection is None:
            return None
        if self._snapshot.state in {
            MediaSelectionState.ANCHOR_RESOLVING,
            MediaSelectionState.FALLBACK_PENDING,
        }:
            self._pending_navigation_delta -= 1
            return None
        current_row = self.current_row()
        if current_row <= 0:
            return None
        return current_row - 1

    def _handle_collection_changed(self) -> None:
        if self._collection is None:
            self._clear_selection()
            return

        stable_path = self._snapshot.path
        stable_asset_id = self._snapshot.asset_id
        if stable_path is not None:
            anchor_status = None
            anchor_status_for = getattr(self._collection, "selection_anchor_status", None)
            if callable(anchor_status_for):
                anchor_status = anchor_status_for(stable_path)
            if anchor_status in {"pending", "retry"}:
                self._pending_fallback_row = None
                self._publish_snapshot(
                    state=MediaSelectionState.ANCHOR_RESOLVING,
                    row=None,
                    path=stable_path,
                    asset_id=stable_asset_id,
                    reason=MediaSelectionChangeReason.ANCHOR_PENDING,
                )
                return

            cached_row_for_path = getattr(self._collection, "cached_row_for_path", None)
            if anchor_status == "missing":
                row = None
            elif callable(cached_row_for_path):
                row = cached_row_for_path(stable_path)
            else:
                # Compatibility collections keep all rows in memory. The real
                # Gallery store always takes the cache-only branch above.
                row = self._collection.row_for_path(stable_path)
            if row is not None:
                dto = self._collection.asset_at(row)
                asset_id = str(dto.id) if dto is not None else stable_asset_id
                self._last_resolved_row = row
                self._pending_fallback_row = None
                self._publish_snapshot(
                    state=MediaSelectionState.RESOLVED,
                    row=row,
                    path=stable_path,
                    asset_id=asset_id,
                    reason=MediaSelectionChangeReason.ANCHOR_RESOLVED,
                )
                self._dispatch_pending_navigation(row)
                return
            if callable(cached_row_for_path) and anchor_status != "missing":
                self._pending_fallback_row = None
                self._publish_snapshot(
                    state=MediaSelectionState.ANCHOR_RESOLVING,
                    row=None,
                    path=stable_path,
                    asset_id=stable_asset_id,
                    reason=MediaSelectionChangeReason.ANCHOR_PENDING,
                )
                return

        count = self._collection.count()
        if count <= 0:
            self._clear_selection()
            return
        current_row = self.current_row()
        row_hint = self._last_resolved_row if self._last_resolved_row >= 0 else current_row
        fallback_row = min(max(row_hint, 0), count - 1)
        self._select_fallback_row(fallback_row)

    def _select_fallback_row(self, fallback_row: int) -> None:
        if self._collection is None:
            return
        dto = self._collection.asset_at(fallback_row)
        if dto is None:
            ensure_row_loaded = getattr(self._collection, "ensure_row_loaded", None)
            if callable(ensure_row_loaded):
                ensure_row_loaded(fallback_row)
            self._pending_fallback_row = fallback_row
            self._publish_snapshot(
                state=MediaSelectionState.FALLBACK_PENDING,
                row=None,
                path=self._snapshot.path,
                asset_id=self._snapshot.asset_id,
                reason=MediaSelectionChangeReason.FALLBACK_PENDING,
            )
            return
        self._last_resolved_row = fallback_row
        self._pending_fallback_row = None
        pin_path = getattr(self._collection, "pin_path", None)
        if callable(pin_path):
            pin_path(dto.abs_path, asset_id=str(dto.id), previous_row=fallback_row)
        self._publish_snapshot(
            state=MediaSelectionState.RESOLVED,
            row=fallback_row,
            path=dto.abs_path,
            asset_id=str(dto.id),
            reason=MediaSelectionChangeReason.FALLBACK_RESOLVED,
        )
        self._dispatch_pending_navigation(fallback_row)

    def _handle_row_loaded(self, row: int) -> None:
        if self._pending_fallback_row == row:
            self._select_fallback_row(row)

    def _clear_selection(self) -> None:
        self._last_resolved_row = -1
        self._pending_fallback_row = None
        self._pending_navigation_delta = 0
        self._publish_snapshot(
            state=MediaSelectionState.NONE,
            row=None,
            path=None,
            asset_id=None,
            reason=MediaSelectionChangeReason.CLEARED,
        )

    def _publish_snapshot(
        self,
        *,
        state: MediaSelectionState,
        row: int | None,
        path: Path | None,
        asset_id: str | None,
        reason: MediaSelectionChangeReason,
        force: bool = False,
    ) -> None:
        previous = self._snapshot
        semantic_state = (state, row, path, asset_id)
        previous_semantic_state = (
            previous.state,
            previous.row,
            previous.path,
            previous.asset_id,
        )
        if not force and semantic_state == previous_semantic_state:
            return
        snapshot = MediaSelectionSnapshot(
            version=previous.version + 1,
            state=state,
            row=row,
            path=Path(path) if path is not None else None,
            asset_id=str(asset_id) if asset_id is not None else None,
        )
        self._snapshot = snapshot
        self.selectionChanged.emit(snapshot, reason)

    def _dispatch_pending_navigation(self, resolved_row: int) -> None:
        if self._collection is None or self._pending_navigation_delta == 0:
            return
        delta = self._pending_navigation_delta
        self._pending_navigation_delta = 0
        target_row = resolved_row + delta
        if target_row < 0 or target_row >= self._collection.count():
            return
        self.navigationRequested.emit(target_row)
