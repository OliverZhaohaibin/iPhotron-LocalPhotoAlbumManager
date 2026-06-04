from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for selection controller tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QAbstractListModel, QItemSelectionModel, QModelIndex, Qt
from PySide6.QtWidgets import QApplication, QPushButton

from iPhoto.gui.ui.controllers.selection_controller import SelectionController
from iPhoto.gui.ui.models.roles import Roles, role_names
from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class _ResettablePathModel(QAbstractListModel):
    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self.paths = list(paths)

    def roleNames(self):  # type: ignore[override]
        return role_names(super().roleNames())

    def rowCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]
        return 0 if parent.isValid() else len(self.paths)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid() or index.row() >= len(self.paths):
            return None
        path = self.paths[index.row()]
        if int(role) == Roles.ABS:
            return str(path)
        if int(role) == Qt.DisplayRole:
            return path.name
        return None

    def row_for_path(self, path: Path) -> int | None:
        try:
            return self.paths.index(Path(path))
        except ValueError:
            return None

    def replace_paths(self, paths: list[Path]) -> None:
        self.beginResetModel()
        self.paths = list(paths)
        self.endResetModel()


def test_selection_controller_restores_selection_across_model_reset(qapp: QApplication) -> None:
    first = Path("/library/a.jpg")
    second = Path("/library/b.jpg")
    model = _ResettablePathModel([first, second])
    grid = GalleryGridView()
    grid.setModel(model)
    grid.show()
    qapp.processEvents()

    controller = SelectionController(
        selection_button=QPushButton(),
        grid_view=grid,
        grid_delegate=None,
        preview_controller=MagicMock(),
        handle_grid_clicks=False,
    )
    controller.set_selection_mode(True)
    grid.selectionModel().select(
        model.index(1, 0),
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )

    model.replace_paths([second, first])
    qapp.processEvents()

    selected_rows = sorted(index.row() for index in grid.selectionModel().selectedIndexes())
    assert selected_rows == [0]
