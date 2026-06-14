"""Regression tests for low-latency Gallery scrolling."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QResizeEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListView

from iPhoto.gui.ui.widgets.asset_grid import AssetGrid


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_grid(qapp: QApplication, rows: int = 500) -> AssetGrid:
    grid = AssetGrid()
    grid.setViewMode(QListView.ViewMode.IconMode)
    grid.setWrapping(True)
    grid.setFlow(QListView.Flow.LeftToRight)
    grid.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    grid.setGridSize(QSize(100, 100))
    grid.setIconSize(QSize(98, 98))
    model = QStandardItemModel()
    for i in range(rows):
        model.appendRow(QStandardItem(f"item-{i}"))
    grid.setModel(model)
    grid.resize(500, 300)
    grid.show()
    qapp.processEvents()
    return grid


def test_scroll_path_never_forces_layout_or_repaint(qapp: QApplication) -> None:
    grid = _make_grid(qapp)

    with (
        patch.object(grid, "executeDelayedItemsLayout") as layout,
        patch.object(grid.viewport(), "repaint") as repaint,
    ):
        AssetGrid.scrollContentsBy(grid, 0, -20)

    layout.assert_not_called()
    repaint.assert_not_called()


def test_resize_path_never_forces_synchronous_repaint(qapp: QApplication) -> None:
    grid = _make_grid(qapp)

    with patch.object(grid.viewport(), "repaint") as repaint:
        event = QResizeEvent(QSize(800, 600), QSize(400, 300))
        AssetGrid.resizeEvent(grid, event)

    repaint.assert_not_called()


def test_visible_rows_use_geometry_without_index_at_probes(qapp: QApplication) -> None:
    grid = _make_grid(qapp, rows=10_000)
    emitted: list[tuple[int, int]] = []
    grid.visibleRowsChanged.connect(lambda first, last: emitted.append((first, last)))

    with patch.object(grid, "indexAt", side_effect=AssertionError("indexAt must not be used")):
        grid._visible_range = None
        grid._emit_visible_rows()

    assert emitted
    first, last = emitted[-1]
    assert first == 0
    assert first <= last < 9_999


class _WheelEvent:
    def __init__(self, *, pixel_y: int = 0, angle_y: int = 0) -> None:
        self._pixel = QPoint(0, pixel_y)
        self._angle = QPoint(0, angle_y)
        self.accepted = False

    def pixelDelta(self) -> QPoint:
        return self._pixel

    def angleDelta(self) -> QPoint:
        return self._angle

    def accept(self) -> None:
        self.accepted = True


def test_trackpad_pixel_delta_is_accumulated_one_to_one(qapp: QApplication) -> None:
    grid = _make_grid(qapp)
    bar = grid.verticalScrollBar()
    bar.setRange(0, 10_000)
    bar.setValue(0)
    first = _WheelEvent(pixel_y=-11)
    second = _WheelEvent(pixel_y=-13)

    assert grid._scroll_controller.handle_wheel(first) is True
    assert grid._scroll_controller.handle_wheel(second) is True
    qapp.processEvents()

    assert first.accepted and second.accepted
    assert bar.value() == 24


def test_discrete_wheel_uses_constant_system_configured_step(qapp: QApplication) -> None:
    grid = _make_grid(qapp)
    bar = grid.verticalScrollBar()
    bar.setRange(0, 10_000)
    bar.setValue(0)

    with patch.object(QApplication, "wheelScrollLines", return_value=3):
        assert grid._scroll_controller.handle_wheel(_WheelEvent(angle_y=-120))
        qapp.processEvents()
        first_value = bar.value()
        assert grid._scroll_controller.handle_wheel(_WheelEvent(angle_y=-120))
        qapp.processEvents()

    assert first_value == 300
    assert bar.value() == 600


def test_discrete_wheel_respects_zero_system_scroll_lines(qapp: QApplication) -> None:
    grid = _make_grid(qapp)
    bar = grid.verticalScrollBar()
    bar.setRange(0, 10_000)
    bar.setValue(500)

    with patch.object(QApplication, "wheelScrollLines", return_value=0):
        assert grid._scroll_controller.handle_wheel(_WheelEvent(angle_y=-120))
        qapp.processEvents()

    assert bar.value() == 500


def test_rapid_back_and_forth_notches_keep_constant_distance(qapp: QApplication) -> None:
    grid = _make_grid(qapp)
    bar = grid.verticalScrollBar()
    bar.setRange(0, 100_000)
    bar.setValue(50_000)
    values = [bar.value()]

    with patch.object(QApplication, "wheelScrollLines", return_value=3):
        for index in range(100):
            angle = -120 if index % 2 == 0 else 120
            assert grid._scroll_controller.handle_wheel(_WheelEvent(angle_y=angle))
            qapp.processEvents()
            values.append(bar.value())

    assert {abs(current - previous) for previous, current in zip(values, values[1:])} == {300}
    assert bar.value() == 50_000
