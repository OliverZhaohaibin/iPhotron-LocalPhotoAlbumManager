"""Tests for the ContextMenuController export functionality."""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, QModelIndex

from iPhotos.src.iPhoto.gui.ui.controllers.context_menu_controller import ContextMenuController


@pytest.fixture
def mock_dependencies():
    return {
        "grid_view": MagicMock(),
        "asset_model": MagicMock(),
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
    grid_view.selectionModel.return_value = selection_model

    controller = ContextMenuController(
        grid_view=mock_dependencies["grid_view"],
        asset_model=mock_dependencies["asset_model"],
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

    # Verify connection
    mock_action = mock_menu.addAction.return_value
    mock_action.triggered.connect.assert_any_call(mock_dependencies["export_callback"])


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
