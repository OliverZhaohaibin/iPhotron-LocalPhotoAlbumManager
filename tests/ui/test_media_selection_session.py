from __future__ import annotations

from pathlib import Path

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.media import MediaRestoreRequest, MediaSelectionSession
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


def test_session_tracks_current_row_and_source() -> None:
    session = MediaSelectionSession()
    collection = _Collection([Path("/fake/a.jpg"), Path("/fake/b.jpg")])
    session.bind_collection(collection)

    source = session.set_current_row(1)

    assert source == Path("/fake/b.jpg")
    assert session.current_row() == 1
    assert session.current_source() == Path("/fake/b.jpg")


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
