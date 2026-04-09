from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for context menu tests", exc_type=ImportError)

from iPhoto.gui.ui.controllers.context_menu_controller import ContextMenuController


def _make_controller(*, selected_paths: list[Path], prepare_cb=None):
    grid_view = MagicMock()
    selection_model = MagicMock()
    selected_index = MagicMock()
    selected_index.isValid.return_value = True
    selected_index.row.return_value = 0
    selection_model.selectedIndexes.return_value = [selected_index]
    grid_view.selectionModel.return_value = selection_model

    facade = MagicMock()
    navigation = MagicMock()
    navigation.is_recently_deleted_view.return_value = False

    controller = ContextMenuController(
        grid_view=grid_view,
        asset_model=MagicMock(),
        selected_paths_provider=MagicMock(return_value=selected_paths),
        facade=facade,
        status_bar=MagicMock(),
        notification_toast=MagicMock(),
        selection_controller=MagicMock(),
        navigation=navigation,
        export_callback=MagicMock(),
        prepare_paths_for_mutation=prepare_cb,
    )
    controller._apply_optimistic_move = MagicMock(return_value=True)  # type: ignore[method-assign]
    return controller, facade


def test_delete_selection_prepares_paths_before_mutation() -> None:
    asset_path = Path("D:/library/video.mp4")
    events: list[str] = []

    def _prepare(paths: list[Path]) -> None:
        assert paths == [asset_path]
        events.append("prepare")

    controller, facade = _make_controller(selected_paths=[asset_path], prepare_cb=_prepare)
    facade.delete_assets.side_effect = lambda paths: events.append("delete")

    assert controller.delete_selection() is True
    assert events == ["prepare", "delete"]


def test_execute_move_to_album_prepares_paths_before_move() -> None:
    asset_path = Path("D:/library/video.mp4")
    destination = Path("D:/library/AlbumB")
    events: list[str] = []

    def _prepare(paths: list[Path]) -> None:
        assert paths == [asset_path]
        events.append("prepare")

    controller, facade = _make_controller(selected_paths=[asset_path], prepare_cb=_prepare)
    facade.move_assets.side_effect = lambda paths, target: events.append(f"move:{target}")

    controller._execute_move_to_album(destination)

    assert events == ["prepare", f"move:{destination}"]
