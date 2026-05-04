"""Tests for the ExportController."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QAction, QActionGroup

from iPhoto.gui.ui.controllers.export_controller import ExportController
from iPhoto.library.runtime_controller import LibraryRuntimeController


@pytest.fixture
def mock_settings():
    settings = MagicMock()

    def _get(key, default=None):
        return {
            "ui.export_destination": "library",
            "ui.export_format": "jpg",
        }.get(key, default)

    settings.get.side_effect = _get
    return settings


@pytest.fixture
def mock_library(tmp_path):
    lib = MagicMock(spec=LibraryRuntimeController)
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
        "format_group": MagicMock(spec=QActionGroup),
        "format_jpg": MagicMock(spec=QAction),
        "format_png": MagicMock(spec=QAction),
        "format_tiff": MagicMock(spec=QAction),
    }


@patch("iPhoto.gui.ui.controllers.export_controller.QThreadPool")
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
        format_group=mock_actions["format_group"],
        format_jpg=mock_actions["format_jpg"],
        format_png=mock_actions["format_png"],
        format_tiff=mock_actions["format_tiff"],
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


@patch("iPhoto.gui.ui.controllers.export_controller.QThreadPool")
@patch("iPhoto.gui.ui.controllers.export_controller.ExportWorker")
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
        format_group=mock_actions["format_group"],
        format_jpg=mock_actions["format_jpg"],
        format_png=mock_actions["format_png"],
        format_tiff=mock_actions["format_tiff"],
        main_window=MagicMock(),
        selection_callback=selection_cb,
    )

    # Trigger
    controller._handle_export_selected()

    # Verify
    mock_worker_cls.assert_called()
    mock_pool.globalInstance().start.assert_called()


@patch("iPhoto.gui.ui.controllers.export_controller.QThreadPool")
@patch("iPhoto.gui.ui.controllers.export_controller.LibraryExportWorker")
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
        format_group=mock_actions["format_group"],
        format_jpg=mock_actions["format_jpg"],
        format_png=mock_actions["format_png"],
        format_tiff=mock_actions["format_tiff"],
        main_window=MagicMock(),
        selection_callback=selection_cb,
    )

    # Trigger
    controller._handle_export_all_edited()

    # Verify
    # We expect tmp_path / "exported" and the format from settings
    mock_worker_cls.assert_called_with(
        mock_library,
        mock_library.root.return_value / "exported",
        "jpg",
    )
    mock_pool.globalInstance().start.assert_called()


@patch("iPhoto.gui.ui.controllers.export_controller.QThreadPool")
def test_handle_format_changed(
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
        format_group=mock_actions["format_group"],
        format_jpg=mock_actions["format_jpg"],
        format_png=mock_actions["format_png"],
        format_tiff=mock_actions["format_tiff"],
        main_window=MagicMock(),
        selection_callback=selection_cb,
    )

    # Trigger PNG selection
    mock_settings.set.reset_mock()
    controller._handle_format_changed(mock_actions["format_png"])
    mock_settings.set.assert_called_once_with("ui.export_format", "png")

    # Trigger TIFF selection
    mock_settings.set.reset_mock()
    controller._handle_format_changed(mock_actions["format_tiff"])
    mock_settings.set.assert_called_once_with("ui.export_format", "tiff")

    # Trigger JPG selection (fallback)
    mock_settings.set.reset_mock()
    controller._handle_format_changed(mock_actions["format_jpg"])
    mock_settings.set.assert_called_once_with("ui.export_format", "jpg")
