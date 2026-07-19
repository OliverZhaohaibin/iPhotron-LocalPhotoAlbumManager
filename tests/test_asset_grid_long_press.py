from unittest.mock import patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="Qt GUI not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtTest", reason="Qt test utilities unavailable", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QStandardItem, QStandardItemModel
from PySide6.QtTest import QSignalSpy

from iPhoto.gui.ui.widgets.asset_grid import AssetGrid


def _activate_long_press(grid: AssetGrid, index) -> None:
    grid._pressed_index = index
    grid._press_pos = QPoint(1, 1)
    grid._on_long_press_timeout()


def _release_event() -> QMouseEvent:
    pos = QPointF(1.0, 1.0)
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        pos,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_asset_grid_long_press_emits_preview(qtbot) -> None:
    grid = AssetGrid()
    qtbot.addWidget(grid)
    model = QStandardItemModel()
    model.appendRow(QStandardItem("item"))
    grid.setModel(model)
    index = model.index(0, 0)

    preview_spy = QSignalSpy(grid.requestPreview)
    release_spy = QSignalSpy(grid.previewReleased)

    _activate_long_press(grid, index)
    assert preview_spy.count() == 1

    grid.mouseReleaseEvent(_release_event())

    assert release_spy.count() == 1


def test_asset_grid_suppresses_first_macos_preview_leave(qtbot) -> None:
    grid = AssetGrid()
    qtbot.addWidget(grid)
    model = QStandardItemModel()
    model.appendRow(QStandardItem("item"))
    grid.setModel(model)
    index = model.index(0, 0)

    preview_spy = QSignalSpy(grid.requestPreview)
    release_spy = QSignalSpy(grid.previewReleased)
    cancel_spy = QSignalSpy(grid.previewCancelled)

    with (
        patch("iPhoto.gui.ui.widgets.asset_grid._IS_DARWIN", True),
        patch(
            "iPhoto.gui.ui.widgets.asset_grid.QApplication.mouseButtons",
            return_value=Qt.MouseButton.LeftButton,
        ),
    ):
        _activate_long_press(grid, index)
        assert preview_spy.count() == 1

        grid.leaveEvent(QEvent(QEvent.Type.Leave))

        assert cancel_spy.count() == 0

        grid.mouseReleaseEvent(_release_event())

    assert release_spy.count() == 1
