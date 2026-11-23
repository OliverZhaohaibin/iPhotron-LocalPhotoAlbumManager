from __future__ import annotations

from pathlib import Path

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.media import (
    MediaRestoreRequest,
    MediaSelectionChangeReason,
    MediaSelectionSession,
    MediaSelectionSnapshot,
    MediaSelectionState,
)
from iPhoto.gui.viewmodels.signal import Signal


class _Collection:
    def __init__(self, paths: list[Path]) -> None:
        self.data_changed = Signal()
        self._paths = list(paths)
        self.ensure_calls: list[int] = []
        self._missing_until_ensured: set[int] = set()

    def count(self) -> int:
        return len(self._paths)

    def asset_at(self, row: int):
        if row < 0 or row >= len(self._paths):
            return None
        if row in self._missing_until_ensured:
            return None
        path = self._paths[row]
        return AssetDTO(
            id=str(row),
            abs_path=path,
            rel_path=Path(path.name),
            media_type="image",
            created_at=None,
            width=0,
            height=0,
            duration=0.0,
            size_bytes=0,
            metadata={},
            is_favorite=False,
        )

    def ensure_row_loaded(self, row: int, *, emit_signals: bool = True) -> bool:
        del emit_signals
        self.ensure_calls.append(row)
        self._missing_until_ensured.discard(row)
        return 0 <= row < len(self._paths)

    def row_for_path(self, path: Path) -> int | None:
        for index, candidate in enumerate(self._paths):
            if candidate == path:
                return index
        return None

    def remove_row(self, row: int) -> None:
        self._paths.pop(row)
        self.data_changed.emit()

    def replace(self, paths: list[Path]) -> None:
        self._paths = list(paths)
        self.data_changed.emit()


class _AnchoredCollection(_Collection):
    def __init__(self, paths: list[Path]) -> None:
        super().__init__(paths)
        self.row_loaded = Signal()
        self.anchor_status: str | None = None
        self.anchor_path: Path | None = None
        self.cached_rows = set(range(len(paths)))
        self.sync_lookup_calls = 0

    def row_for_path(self, path: Path) -> int | None:
        del path
        self.sync_lookup_calls += 1
        raise AssertionError("refresh must not call the synchronous path lookup")

    def asset_at(self, row: int):
        if row not in self.cached_rows:
            return None
        return super().asset_at(row)

    def cached_row_for_path(self, path: Path) -> int | None:
        for index, candidate in enumerate(self._paths):
            if index in self.cached_rows and candidate == path:
                return index
        return None

    def selection_anchor_status(self, path: Path) -> str | None:
        if path == self.anchor_path:
            return self.anchor_status
        return None

    def pin_path(
        self,
        path: Path,
        *,
        asset_id: str = "",
        previous_row: int | None = None,
    ) -> None:
        del asset_id, previous_row
        self.anchor_path = path
        self.anchor_status = "resolved"

    def publish(
        self,
        paths: list[Path],
        *,
        status: str | None,
        cached_rows: set[int],
    ) -> None:
        self._paths = list(paths)
        self.cached_rows = set(cached_rows)
        self.anchor_status = status
        self.data_changed.emit()


def test_session_tracks_current_row_and_source() -> None:
    session = MediaSelectionSession()
    collection = _Collection([Path("/fake/a.jpg"), Path("/fake/b.jpg")])
    session.bind_collection(collection)

    source = session.set_current_row(1)

    assert source == Path("/fake/b.jpg")
    assert session.current_row() == 1
    assert session.current_source() == Path("/fake/b.jpg")


def test_session_publishes_one_coherent_snapshot_per_transition() -> None:
    current = Path("/fake/current.jpg")
    collection = _AnchoredCollection([Path("/fake/a.jpg"), current])
    session = MediaSelectionSession()
    changes: list[tuple[MediaSelectionSnapshot, MediaSelectionChangeReason]] = []
    session.selectionChanged.connect(
        lambda snapshot, reason: changes.append((snapshot, reason))
    )
    session.bind_collection(collection)
    changes.clear()

    session.set_current_row(1)
    selected, selected_reason = changes[-1]
    assert selected_reason is MediaSelectionChangeReason.USER_SELECTED
    assert selected == session.selection_snapshot()
    assert selected.state is MediaSelectionState.RESOLVED
    assert selected.row == 1
    assert selected.path == current
    assert selected.asset_id == "1"

    collection.publish(
        [Path("/fake/new.jpg"), Path("/fake/a.jpg"), current],
        status="retry",
        cached_rows={0, 1, 2},
    )
    pending, pending_reason = changes[-1]
    assert pending_reason is MediaSelectionChangeReason.ANCHOR_PENDING
    assert pending.version == selected.version + 1
    assert pending.state is MediaSelectionState.ANCHOR_RESOLVING
    assert pending.row is None
    assert pending.path == current
    assert pending.asset_id == selected.asset_id


def test_session_ensures_missing_row_before_setting_current() -> None:
    session = MediaSelectionSession()
    collection = _Collection([Path("/fake/a.jpg"), Path("/fake/deep.jpg")])
    collection._missing_until_ensured.add(1)
    session.bind_collection(collection)

    source = session.set_current_row(1)

    assert source == Path("/fake/deep.jpg")
    assert collection.ensure_calls == [1]
    assert session.current_row() == 1
    assert session.current_source() == Path("/fake/deep.jpg")


def test_session_relocates_current_asset_after_rows_removed() -> None:
    current = Path("/fake/b.jpg")
    session = MediaSelectionSession()
    collection = _Collection([Path("/fake/a.jpg"), current, Path("/fake/c.jpg")])
    session.bind_collection(collection)
    session.set_current_row(1)

    collection.remove_row(0)

    assert session.current_row() == 0
    assert session.current_source() == current


def test_session_can_restore_current_item_by_path_after_reload() -> None:
    current = Path("/fake/b.jpg")
    session = MediaSelectionSession()
    collection = _Collection([Path("/fake/a.jpg"), current])
    session.bind_collection(collection)
    session.set_current_row(1)
    collection.replace([current, Path("/fake/c.jpg")])

    assert session.set_current_by_path(current) is True
    assert session.current_row() == 0
    assert session.current_source() == current


def test_session_emits_restore_request_payload() -> None:
    session = MediaSelectionSession()
    emitted: list[MediaRestoreRequest] = []
    session.restoreRequested.connect(emitted.append)

    request = MediaRestoreRequest(path=Path("/fake/video.mp4"), reason="edit_done", duration_sec=7.25)
    session.request_restore(request)

    assert emitted == [request]


def test_scan_refresh_uses_only_anchor_cache_and_preserves_pending_source() -> None:
    current = Path("/fake/current.jpg")
    collection = _AnchoredCollection([Path("/fake/a.jpg"), current])
    session = MediaSelectionSession()
    session.bind_collection(collection)
    session.set_current_row(1)

    collection.publish(
        [Path("/fake/new.jpg"), Path("/fake/a.jpg"), current],
        status="retry",
        cached_rows={0, 1, 2},
    )

    assert session.current_row() == -1
    assert session.current_source() == current
    assert session.selection_state() is MediaSelectionState.ANCHOR_RESOLVING
    assert collection.sync_lookup_calls == 0

    collection.publish(
        [Path("/fake/new.jpg"), Path("/fake/a.jpg"), current],
        status="resolved",
        cached_rows={0, 1, 2},
    )

    assert session.current_row() == 2
    assert session.current_source() == current
    assert session.selection_state() is MediaSelectionState.RESOLVED
    assert collection.sync_lookup_calls == 0


def test_retry_next_waits_for_resolved_anchor_instead_of_jumping_to_zero() -> None:
    current = Path("/fake/current.jpg")
    after = Path("/fake/after.jpg")
    collection = _AnchoredCollection([Path("/fake/a.jpg"), current, after])
    session = MediaSelectionSession()
    requested: list[int] = []
    session.navigationRequested.connect(requested.append)
    session.bind_collection(collection)
    session.set_current_row(1)

    reordered = [Path("/fake/new.jpg"), Path("/fake/a.jpg"), current, after]
    collection.publish(reordered, status="retry", cached_rows={0, 1, 2, 3})

    assert session.next_row() is None
    assert requested == []

    collection.publish(reordered, status="resolved", cached_rows={0, 1, 2, 3})

    assert session.current_row() == 2
    assert requested == [3]


def test_retry_previous_preserves_direction_until_anchor_resolves() -> None:
    before = Path("/fake/before.jpg")
    current = Path("/fake/current.jpg")
    collection = _AnchoredCollection([before, current, Path("/fake/after.jpg")])
    session = MediaSelectionSession()
    requested: list[int] = []
    session.navigationRequested.connect(requested.append)
    session.bind_collection(collection)
    session.set_current_row(1)

    reordered = [Path("/fake/new.jpg"), before, current, Path("/fake/after.jpg")]
    collection.publish(reordered, status="retry", cached_rows={0, 1, 2, 3})

    assert session.previous_row() is None
    assert requested == []

    collection.publish(reordered, status="resolved", cached_rows={0, 1, 2, 3})

    assert session.current_row() == 2
    assert requested == [1]


def test_pending_navigation_clamps_to_last_row_after_anchor_resolves() -> None:
    paths = [Path(f"/fake/{row}.jpg") for row in range(1000)]
    current = paths[998]
    collection = _AnchoredCollection(paths)
    session = MediaSelectionSession()
    requested: list[int] = []
    session.navigationRequested.connect(requested.append)
    session.bind_collection(collection)
    session.set_current_row(998)

    collection.publish(paths, status="retry", cached_rows=set(range(1000)))
    assert session.next_row() is None
    assert session.next_row() is None

    collection.publish(paths, status="resolved", cached_rows=set(range(1000)))

    assert session.current_source() == current
    assert requested == [999]


def test_retry_exhaustion_is_terminal_and_navigation_remains_available() -> None:
    current = Path("/fake/current.jpg")
    collection = _AnchoredCollection(
        [Path("/fake/before.jpg"), current, Path("/fake/after.jpg")]
    )
    session = MediaSelectionSession()
    changes: list[tuple[MediaSelectionSnapshot, MediaSelectionChangeReason]] = []
    requested: list[int] = []
    session.selectionChanged.connect(lambda snapshot, reason: changes.append((snapshot, reason)))
    session.navigationRequested.connect(requested.append)
    session.bind_collection(collection)
    session.set_current_row(1)

    collection.publish(
        collection._paths,
        status="retry",
        cached_rows=set(),
    )
    assert session.next_row() is None
    collection.publish(
        collection._paths,
        status="unresolved",
        cached_rows=set(),
    )

    snapshot, reason = changes[-1]
    assert reason is MediaSelectionChangeReason.ANCHOR_UNRESOLVED
    assert snapshot.state is MediaSelectionState.ANCHOR_UNRESOLVED
    assert snapshot.row is None
    assert snapshot.path == current
    assert requested == [2]
    assert session.previous_row() == 0


def test_confirmed_missing_anchor_falls_back_near_previous_row() -> None:
    current = Path("/fake/current.jpg")
    replacement = Path("/fake/replacement.jpg")
    collection = _AnchoredCollection([Path("/fake/a.jpg"), current])
    session = MediaSelectionSession()
    session.bind_collection(collection)
    session.set_current_row(1)

    collection.publish(
        [Path("/fake/a.jpg"), replacement],
        status="missing",
        cached_rows={0, 1},
    )

    assert session.current_row() == 1
    assert session.current_source() == replacement
    assert collection.sync_lookup_calls == 0


def test_anchor_without_resolution_owner_falls_back_instead_of_stalling() -> None:
    current = Path("/fake/current.jpg")
    replacement = Path("/fake/replacement.jpg")
    collection = _AnchoredCollection([Path("/fake/a.jpg"), current])
    session = MediaSelectionSession()
    session.bind_collection(collection)
    session.set_current_row(1)

    collection.publish(
        [Path("/fake/a.jpg"), replacement],
        status=None,
        cached_rows={0, 1},
    )

    assert session.selection_state() is MediaSelectionState.RESOLVED
    assert session.current_row() == 1
    assert session.current_source() == replacement
    assert collection.sync_lookup_calls == 0


def test_confirmed_missing_anchor_waits_for_async_fallback_row() -> None:
    current = Path("/fake/current.jpg")
    replacement = Path("/fake/replacement.jpg")
    collection = _AnchoredCollection([Path("/fake/a.jpg"), current])
    session = MediaSelectionSession()
    session.bind_collection(collection)
    session.set_current_row(1)

    collection.publish(
        [Path("/fake/a.jpg"), replacement],
        status="missing",
        cached_rows={0},
    )

    assert session.current_row() == -1
    assert session.current_source() == current
    assert session.selection_state() is MediaSelectionState.FALLBACK_PENDING
    assert collection.ensure_calls[-1] == 1

    collection.cached_rows.add(1)
    collection.row_loaded.emit(1)

    assert session.current_row() == 1
    assert session.current_source() == replacement
