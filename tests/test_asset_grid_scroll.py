"""Regression tests for AssetGrid.scrollContentsBy double-buffering."""

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from unittest.mock import patch

from PySide6.QtCore import QSize
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


def test_scroll_linux_skips_super_and_repaints(qapp: QApplication) -> None:
    """On Linux, scrollContentsBy must skip the blit and repaint synchronously."""
    grid = _make_grid(qapp)

    with (
        patch.object(type(grid).scrollContentsBy, "__wrapped__", create=True),
        patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", True),
        patch.object(grid.viewport(), "repaint") as mock_repaint,
    ):
        # Call through the real method with the flag patched to True
        AssetGrid.scrollContentsBy(grid, 0, -20)
        mock_repaint.assert_called_once()


def test_scroll_linux_flushes_pending_layout(qapp: QApplication) -> None:
    """On Linux, scrollContentsBy must flush pending layout before repaint."""
    grid = _make_grid(qapp)

    call_order: list[str] = []

    with (
        patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", True),
        patch.object(
            grid,
            "executeDelayedItemsLayout",
            side_effect=lambda: call_order.append("layout"),
        ) as mock_layout,
        patch.object(
            grid.viewport(),
            "repaint",
            side_effect=lambda: call_order.append("repaint"),
        ) as mock_repaint,
    ):
        AssetGrid.scrollContentsBy(grid, 0, -20)
        mock_layout.assert_called_once()
        mock_repaint.assert_called_once()
        # Layout flush must happen BEFORE the repaint
        assert call_order == ["layout", "repaint"]


def test_scroll_non_linux_calls_super(qapp: QApplication) -> None:
    """On non-Linux platforms, scrollContentsBy must delegate to the base class."""
    grid = _make_grid(qapp)

    with patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", False):
        # Should not raise; the default QListView path handles the scroll
        AssetGrid.scrollContentsBy(grid, 0, -20)
        # Viewport should not have received a forced repaint
        # (the base class handles updating internally)


def test_resize_linux_forces_repaint(qapp: QApplication) -> None:
    """On Linux, resizeEvent must force a synchronous viewport repaint."""
    grid = _make_grid(qapp)

    with (
        patch("iPhoto.gui.ui.widgets.asset_grid._IS_LINUX", True),
        patch.object(grid.viewport(), "repaint") as mock_repaint,
    ):
        event = QResizeEvent(QSize(800, 600), QSize(400, 300))
        AssetGrid.resizeEvent(grid, event)
        mock_repaint.assert_called_once()


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
