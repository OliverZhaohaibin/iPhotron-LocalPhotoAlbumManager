from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, QModelIndex

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.controllers.context_menu_controller import ContextMenuController
from iPhoto.gui.ui.menus.core import MenuContext


def _make_asset(asset_id: str, rel_path: str) -> AssetDTO:
    return AssetDTO(
        id=asset_id,
        abs_path=Path("/library") / rel_path,
        rel_path=Path(rel_path),
        media_type="image",
        created_at=None,
        width=0,
        height=0,
        duration=0.0,
        size_bytes=0,
        metadata={},
        is_favorite=False,
    )


def _make_controller(
    *,
    context: MenuContext,
    selected_assets: list[AssetDTO],
) -> tuple[ContextMenuController, dict[str, object]]:
    grid_view = MagicMock()
    index = MagicMock(spec=QModelIndex)
    index.isValid.return_value = True
    grid_view.indexAt.return_value = index
    grid_view.viewport.return_value.mapToGlobal.return_value = QPoint(10, 10)

    selection_model = MagicMock()
    selection_model.isSelected.return_value = True
    selection_model.selectedIndexes.return_value = [index]
    index.row.return_value = 0
    grid_view.selectionModel.return_value = selection_model

    facade = MagicMock()
    status_bar = MagicMock()
    toast = MagicMock()
    gallery_vm = MagicMock()
    gallery_vm.context_menu_state.return_value = context
    gallery_vm.items_for_rows.return_value = selected_assets

    controller = ContextMenuController(
        grid_view=grid_view,
        asset_model=MagicMock(),
        selected_paths_provider=MagicMock(return_value=[asset.abs_path for asset in selected_assets]),
        facade=facade,
        status_bar=status_bar,
        notification_toast=toast,
        selection_controller=MagicMock(),
        navigation=None,
        export_callback=MagicMock(),
        gallery_viewmodel=gallery_vm,
    )
    return controller, {
        "facade": facade,
        "status_bar": status_bar,
        "toast": toast,
    }


@patch("iPhoto.gui.ui.controllers.context_menu_controller.QMenu")
def test_people_cluster_gallery_menu_shows_set_as_cover(mock_qmenu_cls) -> None:
    asset = _make_asset("asset-1", "album/a.jpg")
    controller, _deps = _make_controller(
        context=MenuContext(
            surface="gallery",
            selection_kind="empty",
            gallery_section="people_cluster_gallery",
            entity_kind="person",
            entity_id="person-a",
            active_root=Path("/library"),
            is_cluster_gallery=True,
        ),
        selected_assets=[asset],
    )

    with patch("iPhoto.gui.ui.controllers.context_menu_controller.PeopleService") as service_cls:
        service_cls.return_value.resolve_cluster_cover_face.return_value = "face-a"
        controller._handle_context_menu(QPoint(10, 10))

    actions_added = [args[0] for args, _ in mock_qmenu_cls.return_value.addAction.call_args_list]
    assert "Set as Cover" in actions_added


@patch("iPhoto.gui.ui.controllers.context_menu_controller.QMenu")
def test_group_cluster_gallery_menu_shows_set_as_cover(mock_qmenu_cls) -> None:
    asset = _make_asset("asset-1", "album/a.jpg")
    controller, _deps = _make_controller(
        context=MenuContext(
            surface="gallery",
            selection_kind="empty",
            gallery_section="people_cluster_gallery",
            entity_kind="group",
            entity_id="group-a",
            active_root=Path("/library"),
            is_cluster_gallery=True,
        ),
        selected_assets=[asset],
    )

    with patch("iPhoto.gui.ui.controllers.context_menu_controller.PeopleService") as service_cls:
        service_cls.return_value.resolve_group_cover_asset.return_value = "asset-1"
        controller._handle_context_menu(QPoint(10, 10))

    actions_added = [args[0] for args, _ in mock_qmenu_cls.return_value.addAction.call_args_list]
    assert "Set as Cover" in actions_added


@patch("iPhoto.gui.ui.controllers.context_menu_controller.QMenu")
@pytest.mark.parametrize(
    ("section", "entity_kind"),
    [
        ("all_photos", None),
        ("favorites", None),
        ("videos", None),
        ("cluster_gallery", None),
        ("recently_deleted", None),
    ],
)
def test_non_cover_gallery_menu_hides_set_as_cover(
    mock_qmenu_cls,
    section: str,
    entity_kind: str | None,
) -> None:
    asset = _make_asset("asset-1", "album/a.jpg")
    controller, _deps = _make_controller(
        context=MenuContext(
            surface="gallery",
            selection_kind="empty",
            gallery_section=section,
            entity_kind=entity_kind,
            active_root=Path("/library"),
            is_recently_deleted=section == "recently_deleted",
            is_cluster_gallery=section == "cluster_gallery",
        ),
        selected_assets=[asset],
    )

    controller._handle_context_menu(QPoint(10, 10))

    actions_added = [args[0] for args, _ in mock_qmenu_cls.return_value.addAction.call_args_list]
    assert "Set as Cover" not in actions_added
