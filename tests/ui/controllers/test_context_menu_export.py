"""Tests for the ContextMenuController export functionality."""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, QModelIndex

from iPhoto.gui.ui.controllers.context_menu_controller import ContextMenuController


@pytest.fixture
def mock_dependencies():
    return {
        "grid_view": MagicMock(),
        "asset_model": MagicMock(),
        "selected_paths_provider": MagicMock(return_value=[]),
        "facade": MagicMock(),
        "navigation": MagicMock(),
        "status_bar": MagicMock(),
        "notification_toast": MagicMock(),
        "selection_controller": MagicMock(),
        "export_callback": MagicMock(),
    }


def test_init_accepts_callback(mock_dependencies):
    """Verify __init__ accepts export_callback."""
    # Should not raise TypeError
    ContextMenuController(
        grid_view=mock_dependencies["grid_view"],
        asset_model=mock_dependencies["asset_model"],
        selected_paths_provider=mock_dependencies["selected_paths_provider"],
        facade=mock_dependencies["facade"],
        navigation=mock_dependencies["navigation"],
        status_bar=mock_dependencies["status_bar"],
        notification_toast=mock_dependencies["notification_toast"],
        selection_controller=mock_dependencies["selection_controller"],
        export_callback=mock_dependencies["export_callback"],
    )


@patch("iPhoto.gui.ui.controllers.context_menu_controller.QMenu")
def test_export_action_present_when_selected(mock_qmenu_cls, mock_dependencies):
    """Verify 'Export' action is added when items are selected."""

    # Setup mocks for selection
    grid_view = mock_dependencies["grid_view"]
    index = MagicMock(spec=QModelIndex)
    index.isValid.return_value = True
    grid_view.indexAt.return_value = index

    selection_model = MagicMock()
    selection_model.isSelected.return_value = True
    selection_model.selectedIndexes.return_value = [index]
    index.row.return_value = 0
    grid_view.selectionModel.return_value = selection_model

    controller = ContextMenuController(
        grid_view=mock_dependencies["grid_view"],
        asset_model=mock_dependencies["asset_model"],
        selected_paths_provider=mock_dependencies["selected_paths_provider"],
        facade=mock_dependencies["facade"],
        navigation=mock_dependencies["navigation"],
        status_bar=mock_dependencies["status_bar"],
        notification_toast=mock_dependencies["notification_toast"],
        selection_controller=mock_dependencies["selection_controller"],
        export_callback=mock_dependencies["export_callback"],
    )

    # Mock the menu instance
    mock_menu = mock_qmenu_cls.return_value

    # Trigger context menu
    controller._handle_context_menu(QPoint(10, 10))

    # Verify "Export" action is added
    actions_added = [args[0] for args, _ in mock_menu.addAction.call_args_list]
    assert "Export" in actions_added, f"Export action not found in {actions_added}"


@patch("iPhoto.gui.ui.controllers.context_menu_controller.QMenu")
def test_export_action_absent_when_no_selection(mock_qmenu_cls, mock_dependencies):
    """Verify 'Export' action is NOT added when no items are selected."""

    grid_view = mock_dependencies["grid_view"]
    # No selection
    index = MagicMock(spec=QModelIndex)
    index.isValid.return_value = False
    grid_view.indexAt.return_value = index

    controller = ContextMenuController(
        grid_view=mock_dependencies["grid_view"],
        asset_model=mock_dependencies["asset_model"],
        selected_paths_provider=mock_dependencies["selected_paths_provider"],
        facade=mock_dependencies["facade"],
        navigation=mock_dependencies["navigation"],
        status_bar=mock_dependencies["status_bar"],
        notification_toast=mock_dependencies["notification_toast"],
        selection_controller=mock_dependencies["selection_controller"],
        export_callback=mock_dependencies["export_callback"],
    )

    mock_menu = mock_qmenu_cls.return_value
    controller._handle_context_menu(QPoint(10, 10))

    actions_added = [args[0] for args, _ in mock_menu.addAction.call_args_list]
    assert "Export" not in actions_added, f"Export action found in {actions_added} but shouldn't be"


@patch("iPhoto.gui.ui.controllers.context_menu_controller.QMenu")
def test_blank_area_menu_ignores_existing_selection(mock_qmenu_cls, mock_dependencies):
    grid_view = mock_dependencies["grid_view"]
    clicked_index = MagicMock(spec=QModelIndex)
    clicked_index.isValid.return_value = False
    grid_view.indexAt.return_value = clicked_index

    selection_model = MagicMock()
    selected_index = MagicMock(spec=QModelIndex)
    selected_index.isValid.return_value = True
    selected_index.row.return_value = 0
    selection_model.selectedIndexes.return_value = [selected_index]
    grid_view.selectionModel.return_value = selection_model

    controller = ContextMenuController(
        grid_view=mock_dependencies["grid_view"],
        asset_model=mock_dependencies["asset_model"],
        selected_paths_provider=mock_dependencies["selected_paths_provider"],
        facade=mock_dependencies["facade"],
        navigation=mock_dependencies["navigation"],
        status_bar=mock_dependencies["status_bar"],
        notification_toast=mock_dependencies["notification_toast"],
        selection_controller=mock_dependencies["selection_controller"],
        export_callback=mock_dependencies["export_callback"],
    )

    mock_menu = mock_qmenu_cls.return_value
    controller._handle_context_menu(QPoint(10, 10))

    actions_added = [args[0] for args, _ in mock_menu.addAction.call_args_list]
    assert "Paste" in actions_added, f"Paste action not found in {actions_added}"
    assert "Copy" not in actions_added, f"Copy action found in {actions_added} but shouldn't be"


def test_selected_asset_paths_are_resolved_via_provider(mock_dependencies):
    controller = ContextMenuController(
        grid_view=mock_dependencies["grid_view"],
        asset_model=mock_dependencies["asset_model"],
        selected_paths_provider=mock_dependencies["selected_paths_provider"],
        facade=mock_dependencies["facade"],
        navigation=mock_dependencies["navigation"],
        status_bar=mock_dependencies["status_bar"],
        notification_toast=mock_dependencies["notification_toast"],
        selection_controller=mock_dependencies["selection_controller"],
        export_callback=mock_dependencies["export_callback"],
    )

    selection_model = MagicMock()
    first = MagicMock()
    first.isValid.return_value = True
    first.row.return_value = 7
    duplicate = MagicMock()
    duplicate.isValid.return_value = True
    duplicate.row.return_value = 7
    second = MagicMock()
    second.isValid.return_value = True
    second.row.return_value = 9
    selection_model.selectedIndexes.return_value = [first, duplicate, second]
    mock_dependencies["grid_view"].selectionModel.return_value = selection_model

    controller._selected_asset_paths()

    mock_dependencies["selected_paths_provider"].assert_called_once_with([7, 9])
