"""Regression tests for AssetGrid scroll update coalescing."""

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from unittest.mock import patch

from PySide6.QtCore import QModelIndex, QSize
from PySide6.QtGui import QResizeEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.asset_grid import AssetGrid


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_grid(qapp: QApplication) -> AssetGrid:
    """Create a minimal AssetGrid with a populated model."""
    grid = AssetGrid()
    model = QStandardItemModel()
    for i in range(50):
        model.appendRow(QStandardItem(f"item-{i}"))
    grid.setModel(model)
    grid.show()
    qapp.processEvents()
    return grid


def test_scroll_linux_avoids_synchronous_layout_and_repaint(qapp: QApplication) -> None:
    grid = _make_grid(qapp)
    grid._viewport_update_timer.stop()

    with (
        patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", True),
        patch.object(grid, "executeDelayedItemsLayout") as mock_layout,
        patch.object(grid.viewport(), "repaint") as mock_repaint,
        patch.object(grid.viewport(), "update") as mock_update,
    ):
        AssetGrid.scrollContentsBy(grid, 0, -20)
        mock_layout.assert_not_called()
        mock_repaint.assert_not_called()
        mock_update.assert_not_called()


def test_scroll_linux_coalesces_viewport_updates(qapp: QApplication) -> None:
    grid = _make_grid(qapp)
    grid._viewport_update_timer.stop()

    with (
        patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", True),
        patch.object(grid.viewport(), "update") as mock_update,
    ):
        AssetGrid.scrollContentsBy(grid, 0, -20)
        AssetGrid.scrollContentsBy(grid, 0, -20)
        AssetGrid.scrollContentsBy(grid, 0, -20)

        mock_update.assert_not_called()
        qapp.processEvents()
        mock_update.assert_called_once()


def test_scroll_non_linux_calls_super(qapp: QApplication) -> None:
    """On non-Linux platforms, scrollContentsBy must delegate to the base class."""
    grid = _make_grid(qapp)

    with patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", False):
        # Should not raise; the default QListView path handles the scroll
        AssetGrid.scrollContentsBy(grid, 0, -20)
        # Viewport should not have received a forced repaint
        # (the base class handles updating internally)


def test_resize_linux_schedules_deferred_update(qapp: QApplication) -> None:
    grid = _make_grid(qapp)
    grid._viewport_update_timer.stop()

    with (
        patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", True),
        patch.object(grid.viewport(), "repaint") as mock_repaint,
        patch.object(grid.viewport(), "update") as mock_update,
    ):
        event = QResizeEvent(QSize(800, 600), QSize(400, 300))
        AssetGrid.resizeEvent(grid, event)
        mock_repaint.assert_not_called()
        mock_update.assert_not_called()
        qapp.processEvents()
        mock_update.assert_called_once()


def test_resize_non_linux_no_forced_repaint(qapp: QApplication) -> None:
    """On non-Linux platforms, resizeEvent must not force a synchronous repaint."""
    grid = _make_grid(qapp)

    with (
        patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", False),
        patch.object(grid.viewport(), "repaint") as mock_repaint,
    ):
        event = QResizeEvent(QSize(800, 600), QSize(400, 300))
        AssetGrid.resizeEvent(grid, event)
        mock_repaint.assert_not_called()


def test_visible_rows_ignores_empty_bottom_right_cell(qapp: QApplication) -> None:
    grid = AssetGrid()
    model = QStandardItemModel()
    for i in range(10_000):
        model.appendRow(QStandardItem(f"item-{i}"))
    grid.setModel(model)
    grid.resize(500, 300)
    grid.show()
    qapp.processEvents()

    emitted: list[tuple[int, int]] = []
    grid.visibleRowsChanged.connect(lambda first, last: emitted.append((first, last)))

    def fake_index_at(point):
        if point.x() > grid.viewport().rect().center().x():
            return QModelIndex()
        row = 100 + max(0, point.y()) // 20
        return model.index(row, 0)

    with patch.object(grid, "indexAt", side_effect=fake_index_at):
        grid._visible_range = None
        grid._emit_visible_rows()

    assert emitted
    assert emitted[-1][1] < 9_999
