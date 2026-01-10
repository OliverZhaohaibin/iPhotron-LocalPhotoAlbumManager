"""Tests for playlist reload guard and path-stable selection utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt  # type: ignore  # noqa: E402

from iPhoto.gui.ui.controllers.edit_controller import EditController  # noqa: E402
from iPhoto.gui.ui.media.playlist_controller import PlaylistController  # noqa: E402
from iPhoto.gui.ui.models.asset_model import Roles  # noqa: E402


class _DummyModel(QAbstractListModel):
    def __init__(self, rows: list[dict], album_root: Path):
        super().__init__()
        self._rows = rows
        self._album_root = album_root

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        return 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:  # type: ignore[override]
        if not index.isValid():
            return None
        try:
            row = self._rows[index.row()]
        except IndexError:
            return None
        return row.get(role)

    def index(self, row: int, column: int = 0, parent: QModelIndex | None = None) -> QModelIndex:  # type: ignore[override]
        return super().index(row, column, parent)

    def source_model(self):
        return self

    def album_root(self) -> Path:
        return self._album_root

    def reorder(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


def test_set_current_by_path_restores_selection_after_reorder(qapp, tmp_path: Path) -> None:
    album_root = tmp_path
    path_a = album_root / "a.jpg"
    path_b = album_root / "b.jpg"
    rows = [
        {Roles.ABS: str(path_a), Roles.REL: "a.jpg", Roles.IS_LIVE: False},
        {Roles.ABS: str(path_b), Roles.REL: "b.jpg", Roles.IS_LIVE: False},
    ]
    model = _DummyModel(rows, album_root)
    playlist = PlaylistController()
    playlist.bind_model(model)

    assert playlist.set_current(0) == path_a
    assert playlist.current_source() == path_a

    model.reorder([rows[1], rows[0]])
    # The selection may now point at the moved row; ensure the helper restores it.
    assert playlist.set_current_by_path(path_a)
    assert playlist.current_source() == path_a


def test_reload_guard_suppresses_and_restores(qapp, tmp_path: Path) -> None:
    album_root = tmp_path
    source_path = album_root / "a.jpg"

    class _FakeViewController:
        def __init__(self):
            self.active = True

        def is_edit_view_active(self) -> bool:
            return self.active

    class _FakePlaylist:
        def __init__(self):
            self.calls: list[Path] = []

        def set_current_by_path(self, path: Path) -> bool:
            self.calls.append(path)
            return True

    class _FakeSourceModel:
        def album_root(self) -> Path:
            return album_root

    class _FakeAssetModel:
        def source_model(self) -> _FakeSourceModel:
            return _FakeSourceModel()

    controller = EditController.__new__(EditController)
    controller._view_controller = _FakeViewController()
    controller._playlist = _FakePlaylist()
    controller._asset_model = _FakeAssetModel()
    controller._suppress_playlist_changes = False
    controller._guarded_reload_roots = set()
    controller._current_source = source_path
    controller._paths_equal = EditController._paths_equal  # type: ignore[assignment]
    controller.leave_edit_mode = lambda animate=True: setattr(controller, "_left", animate)

    controller.handle_model_reload_started(album_root)
    assert controller._suppress_playlist_changes is True
    assert album_root in controller._guarded_reload_roots

    controller.handle_model_reload_finished(album_root, True)
    assert controller._suppress_playlist_changes is False
    assert getattr(controller, "_left", None) is None
    assert controller._playlist.calls == [source_path]


def test_reload_guard_handles_failure(qapp, tmp_path: Path) -> None:
    album_root = tmp_path

    class _FakeViewController:
        def is_edit_view_active(self) -> bool:
            return True

    class _FakePlaylist:
        def set_current_by_path(self, path: Path) -> bool:
            return False

    class _FakeSourceModel:
        def album_root(self) -> Path:
            return album_root

    class _FakeAssetModel:
        def source_model(self) -> _FakeSourceModel:
            return _FakeSourceModel()

    controller = EditController.__new__(EditController)
    controller._view_controller = _FakeViewController()
    controller._playlist = _FakePlaylist()
    controller._asset_model = _FakeAssetModel()
    controller._suppress_playlist_changes = False
    controller._guarded_reload_roots = set()
    controller._current_source = album_root / "missing.jpg"
    controller._paths_equal = EditController._paths_equal  # type: ignore[assignment]
    controller.leave_edit_mode = lambda animate=True: setattr(controller, "_left", animate)

    controller.handle_model_reload_started(album_root)
    controller.handle_model_reload_finished(album_root, False)

    assert controller._suppress_playlist_changes is False
    assert getattr(controller, "_left", None) is False
