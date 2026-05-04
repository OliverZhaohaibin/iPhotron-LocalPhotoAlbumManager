"""Non-UI unit tests for export-format wiring in ExportController.

These tests live under ``tests/`` (not ``tests/ui/``) so they are included in
the default test run regardless of the ``--ignore=tests/ui`` pytest flag.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QAction, QActionGroup

from iPhoto.gui.ui.controllers.export_controller import ExportController
from iPhoto.library.runtime_controller import LibraryRuntimeController


def _make_settings(destination: str = "library", export_format: str = "jpg"):
    """Return a mock settings that dispatches *get()* by key."""
    settings = MagicMock()

    def _get(key, default=None):
        return {
            "ui.export_destination": destination,
            "ui.export_format": export_format,
        }.get(key, default)

    settings.get.side_effect = _get
    return settings


def _make_actions():
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


def _build_controller(settings=None, actions=None, tmp_path=None):
    settings = settings or _make_settings()
    actions = actions or _make_actions()
    lib = MagicMock(spec=LibraryRuntimeController)
    lib.root.return_value = tmp_path or Path("/fake/lib")
    return ExportController(
        settings=settings,
        library=lib,
        status_bar=MagicMock(),
        toast=MagicMock(),
        export_all_action=actions["export_all"],
        export_selected_action=actions["export_selected"],
        destination_group=actions["group"],
        destination_library=actions["library"],
        destination_ask=actions["ask"],
        format_group=actions["format_group"],
        format_jpg=actions["format_jpg"],
        format_png=actions["format_png"],
        format_tiff=actions["format_tiff"],
        main_window=MagicMock(),
        selection_callback=MagicMock(),
    ), settings, actions, lib


# ── restore_preference ──────────────────────────────────────────────────────

@patch("iPhoto.gui.ui.controllers.export_controller.QThreadPool")
class TestRestoreFormatPreference:
    def test_restore_jpg(self, _pool) -> None:
        ctrl, _, actions, _ = _build_controller(
            settings=_make_settings(export_format="jpg")
        )
        actions["format_jpg"].setChecked.assert_called_with(True)

    def test_restore_png(self, _pool) -> None:
        ctrl, _, actions, _ = _build_controller(
            settings=_make_settings(export_format="png")
        )
        actions["format_png"].setChecked.assert_called_with(True)

    def test_restore_tiff(self, _pool) -> None:
        ctrl, _, actions, _ = _build_controller(
            settings=_make_settings(export_format="tiff")
        )
        actions["format_tiff"].setChecked.assert_called_with(True)


# ── _handle_format_changed ──────────────────────────────────────────────────

@patch("iPhoto.gui.ui.controllers.export_controller.QThreadPool")
class TestHandleFormatChanged:
    def test_select_png(self, _pool) -> None:
        ctrl, settings, actions, _ = _build_controller()
        settings.set.reset_mock()
        ctrl._handle_format_changed(actions["format_png"])
        settings.set.assert_called_once_with("ui.export_format", "png")

    def test_select_tiff(self, _pool) -> None:
        ctrl, settings, actions, _ = _build_controller()
        settings.set.reset_mock()
        ctrl._handle_format_changed(actions["format_tiff"])
        settings.set.assert_called_once_with("ui.export_format", "tiff")

    def test_select_jpg(self, _pool) -> None:
        ctrl, settings, actions, _ = _build_controller()
        settings.set.reset_mock()
        ctrl._handle_format_changed(actions["format_jpg"])
        settings.set.assert_called_once_with("ui.export_format", "jpg")


# ── format passed into workers ──────────────────────────────────────────────

@patch("iPhoto.gui.ui.controllers.export_controller.QThreadPool")
@patch("iPhoto.gui.ui.controllers.export_controller.LibraryExportWorker")
class TestFormatPassedToWorker:
    def test_export_all_passes_format(self, mock_worker_cls, _pool, tmp_path) -> None:
        settings = _make_settings(export_format="png")
        ctrl, _, _, lib = _build_controller(settings=settings, tmp_path=tmp_path)
        ctrl._handle_export_all_edited()
        mock_worker_cls.assert_called_with(
            lib, tmp_path / "exported", "png"
        )
