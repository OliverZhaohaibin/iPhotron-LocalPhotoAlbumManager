"""Tests for the ExportController."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QAction, QActionGroup

from iPhotos.src.iPhoto.gui.ui.controllers.export_controller import ExportController
from iPhotos.src.iPhoto.library.manager import LibraryManager


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.get.return_value = "library"
    return settings


@pytest.fixture
def mock_library(tmp_path):
    lib = MagicMock(spec=LibraryManager)
    lib.root.return_value = tmp_path
    return lib


@pytest.fixture
def mock_status_bar():
    return MagicMock()


@pytest.fixture
def mock_toast():
    return MagicMock()


@pytest.fixture
def mock_actions():
    return {
        "export_all": MagicMock(spec=QAction),
        "export_selected": MagicMock(spec=QAction),
        "group": MagicMock(spec=QActionGroup),
        "library": MagicMock(spec=QAction),
        "ask": MagicMock(spec=QAction),
    }


@patch("iPhotos.src.iPhoto.gui.ui.controllers.export_controller.QThreadPool")
def test_export_controller_init(
    mock_pool, mock_settings, mock_library, mock_status_bar, mock_toast, mock_actions
):
    selection_cb = MagicMock()

    controller = ExportController(
        settings=mock_settings,
        library=mock_library,
        status_bar=mock_status_bar,
        toast=mock_toast,
        export_all_action=mock_actions["export_all"],
        export_selected_action=mock_actions["export_selected"],
        destination_group=mock_actions["group"],
        destination_library=mock_actions["library"],
        destination_ask=mock_actions["ask"],
        main_window=MagicMock(),
        selection_callback=selection_cb,
    )

    # Check connections
    mock_actions["export_all"].triggered.connect.assert_called_with(
        controller._handle_export_all_edited
    )
    mock_actions["export_selected"].triggered.connect.assert_called_with(
        controller._handle_export_selected
    )

    # Check restore
    mock_actions["library"].setChecked.assert_called_with(True)


@patch("iPhotos.src.iPhoto.gui.ui.controllers.export_controller.QThreadPool")
@patch("iPhotos.src.iPhoto.gui.ui.controllers.export_controller.ExportWorker")
def test_handle_export_selected(
    mock_worker_cls,
    mock_pool,
    mock_settings,
    mock_library,
    mock_status_bar,
    mock_toast,
    mock_actions,
):
    selection_cb = MagicMock(return_value=[Path("/lib/img.jpg")])

    controller = ExportController(
        settings=mock_settings,
        library=mock_library,
        status_bar=mock_status_bar,
        toast=mock_toast,
        export_all_action=mock_actions["export_all"],
        export_selected_action=mock_actions["export_selected"],
        destination_group=mock_actions["group"],
        destination_library=mock_actions["library"],
        destination_ask=mock_actions["ask"],
        main_window=MagicMock(),
        selection_callback=selection_cb,
    )

    # Trigger
    controller._handle_export_selected()

    # Verify
    mock_worker_cls.assert_called()
    mock_pool.globalInstance().start.assert_called()


@patch("iPhotos.src.iPhoto.gui.ui.controllers.export_controller.QThreadPool")
@patch("iPhotos.src.iPhoto.gui.ui.controllers.export_controller.LibraryExportWorker")
def test_handle_export_all_edited(
    mock_worker_cls,
    mock_pool,
    mock_settings,
    mock_library,
    mock_status_bar,
    mock_toast,
    mock_actions,
):
    selection_cb = MagicMock()

    controller = ExportController(
        settings=mock_settings,
        library=mock_library,
        status_bar=mock_status_bar,
        toast=mock_toast,
        export_all_action=mock_actions["export_all"],
        export_selected_action=mock_actions["export_selected"],
        destination_group=mock_actions["group"],
        destination_library=mock_actions["library"],
        destination_ask=mock_actions["ask"],
        main_window=MagicMock(),
        selection_callback=selection_cb,
    )

    # Trigger
    controller._handle_export_all_edited()

    # Verify
    # We expect tmp_path / "exported"
    mock_worker_cls.assert_called_with(mock_library, mock_library.root.return_value / "exported")
    mock_pool.globalInstance().start.assert_called()
