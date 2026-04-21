from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for context menu tests", exc_type=ImportError)

from iPhoto.gui.ui.controllers import context_menu_controller as context_menu_module
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


def test_handle_context_menu_adds_people_cover_action_only_for_people_cluster_gallery(
    monkeypatch,
) -> None:
    asset_path = Path("D:/library/photo.jpg")
    recorded_labels: list[str] = []

    class _FakeSignal:
        def connect(self, _callback) -> None:
            return None

    class _FakeAction:
        def __init__(self, text: str) -> None:
            self._text = text
            self.triggered = _FakeSignal()

        def text(self) -> str:
            return self._text

        def setVisible(self, _visible: bool) -> None:
            return None

    class _FakeSubMenu:
        def __init__(self) -> None:
            self._menu_action = _FakeAction("Move to")

        def addAction(self, text: str):
            return _FakeAction(text)

        def setEnabled(self, _enabled: bool) -> None:
            return None

        def menuAction(self):
            return self._menu_action

    class _FakeMenu:
        def __init__(self, _parent) -> None:
            self._actions: list[_FakeAction] = []

        def setAttribute(self, *_args, **_kwargs) -> None:
            return None

        def setAutoFillBackground(self, *_args, **_kwargs) -> None:
            return None

        def setWindowFlags(self, flags):
            return flags

        def windowFlags(self):
            return 0

        def setPalette(self, *_args, **_kwargs) -> None:
            return None

        def setBackgroundRole(self, *_args, **_kwargs) -> None:
            return None

        def setStyleSheet(self, *_args, **_kwargs) -> None:
            return None

        def setGraphicsEffect(self, *_args, **_kwargs) -> None:
            return None

        def addAction(self, text: str):
            action = _FakeAction(text)
            self._actions.append(action)
            return action

        def addMenu(self, _text: str):
            return _FakeSubMenu()

        def addSeparator(self) -> None:
            return None

        def exec(self, _global_pos) -> None:
            recorded_labels[:] = [action.text() for action in self._actions]

    monkeypatch.setattr(context_menu_module, "QMenu", _FakeMenu)

    controller, _facade = _make_controller(selected_paths=[asset_path])
    controller._collect_move_targets = MagicMock(return_value=[])  # type: ignore[method-assign]

    selection_model = controller._grid_view.selectionModel.return_value
    index = MagicMock()
    index.isValid.return_value = True
    index.row.return_value = 0
    index.data.return_value = "asset-1"
    selection_model.isSelected.return_value = True
    controller._grid_view.indexAt.return_value = index
    controller._grid_view.viewport.return_value.mapToGlobal.return_value = object()
    controller._grid_view.window.return_value = None

    controller._people_cover_context_provider = lambda: ("person", "person-a")
    controller._handle_context_menu(MagicMock())
    assert "Set as Cover" in recorded_labels

    controller._people_cover_context_provider = lambda: None
    controller._handle_context_menu(MagicMock())
    assert "Set as Cover" not in recorded_labels
