from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for media session tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="QtGui is required for media session tests", exc_type=ImportError)

from PySide6.QtGui import QStandardItem, QStandardItemModel

from iPhoto.gui.ui.media import MediaPlaybackSession
from iPhoto.gui.ui.models.roles import Roles


def _make_model(paths: list[Path]) -> QStandardItemModel:
    model = QStandardItemModel()
    for path in paths:
        item = QStandardItem(path.name)
        item.setData(str(path), Roles.ABS)
        item.setData(path.name, Roles.REL)
        model.appendRow(item)
    return model


def test_session_tracks_current_row_and_source() -> None:
    session = MediaPlaybackSession()
    model = _make_model([Path("/fake/a.jpg"), Path("/fake/b.jpg")])
    session.bind_model(model)

    source = session.set_current_row(1)

    assert source == Path("/fake/b.jpg")
    assert session.current_row() == 1
    assert session.current_source() == Path("/fake/b.jpg")


def test_session_relocates_current_asset_after_rows_removed() -> None:
    current = Path("/fake/b.jpg")
    session = MediaPlaybackSession()
    model = _make_model([Path("/fake/a.jpg"), current, Path("/fake/c.jpg")])
    session.bind_model(model)
    session.set_current_row(1)

    model.removeRow(0)

    assert session.current_row() == 0
    assert session.current_source() == current


def test_session_can_restore_current_item_by_path_after_rebinding_model() -> None:
    current = Path("/fake/b.jpg")
    session = MediaPlaybackSession()
    session.bind_model(_make_model([Path("/fake/a.jpg"), current]))
    session.set_current_row(1)
    session.bind_model(_make_model([current, Path("/fake/c.jpg")]))

    assert session.set_current_by_path(current) is True
    assert session.current_row() == 0
    assert session.current_source() == current
