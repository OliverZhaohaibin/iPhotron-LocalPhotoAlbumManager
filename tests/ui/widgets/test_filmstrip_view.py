from pathlib import Path

import pytest
from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelectionModel,
    QModelIndex,
    QStringListModel,
    Qt,
)
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.models.spacer_proxy_model import SpacerProxyModel
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView


class _AssetListModel(QAbstractListModel):
    def __init__(self, paths: list[Path], current_path: Path) -> None:
        super().__init__()
        self._paths = paths
        self._current_path = current_path

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._paths)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._paths)):
            return None
        path = self._paths[index.row()]
        if role == Qt.DisplayRole:
            return path.name
        if role == Roles.ABS:
            return str(path)
        if role == Roles.ASSET_ID:
            return path.stem
        if role == Roles.IS_CURRENT:
            return path == self._current_path
        if role == Roles.IS_SPACER:
            return False
        return None

    def cached_row_for_path(self, path: Path) -> int | None:
        try:
            return self._paths.index(path)
        except ValueError:
            return None

    def publish_scan_revision(self, paths: list[Path]) -> None:
        # GalleryCollectionStore has already replaced its cache by the time
        # GalleryListModelAdapter begins the Qt reset; mirror that ordering.
        self._paths = paths
        self.beginResetModel()
        self.endResetModel()

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_filmstrip_view_has_scrollbar_style(qapp):
    view = FilmstripView()
    style = view.styleSheet()

    assert "QScrollBar" in style, "FilmstripView should have QScrollBar styling"
    assert "background-color: transparent" in style

def test_filmstrip_view_updates_style_on_palette_change(qapp):
    view = FilmstripView()

    # Change palette to something distinct
    palette = view.palette()
    test_color = QColor("#123456")
    palette.setColor(QPalette.ColorRole.WindowText, test_color)
    view.setPalette(palette)

    new_style = view.styleSheet()

    assert "QScrollBar" in new_style

    # Check for the expected track color (alpha=30)
    # modern_scrollbar_style sets track alpha to 30
    expected_track_color = QColor(test_color)
    expected_track_color.setAlpha(30)
    expected_hex = expected_track_color.name(QColor.NameFormat.HexArgb)

    assert expected_hex in new_style, f"Stylesheet should contain the updated track color {expected_hex}"


def test_center_on_index_overrides_pending_restore(qapp):
    view = FilmstripView()
    model = QStringListModel([str(i) for i in range(20)])
    view.setModel(model)
    view.resize(800, 132)
    view.show()
    qapp.processEvents()

    selected = model.index(5, 0)
    stale = model.index(12, 0)
    view.selectionModel().setCurrentIndex(selected, QItemSelectionModel.ClearAndSelect)

    view._pending_scroll_value = 0
    view._pending_center_row = stale.row()
    view._schedule_restore_scroll("test")
    view.center_on_index(selected)
    qapp.processEvents()

    assert view.selectionModel().currentIndex().row() == selected.row()


def test_scan_reset_restores_same_asset_at_same_viewport_position(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(40)]
    current_path = paths[12]
    model = _AssetListModel(paths, current_path)
    proxy = SpacerProxyModel()
    proxy.setSourceModel(model)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(proxy)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    selected = proxy.mapFromSource(model.index(12, 0))
    view.selectionModel().setCurrentIndex(selected, QItemSelectionModel.ClearAndSelect)
    view.center_on_index(selected)
    qapp.processEvents()
    anchor_x = int(view.visualRect(selected).center().x())

    reordered = paths[:]
    reordered.remove(current_path)
    reordered.insert(21, current_path)
    model.publish_scan_revision(reordered)
    qapp.processEvents()

    restored = view.selectionModel().currentIndex()
    assert restored.data(Roles.ABS) == str(current_path)
    assert restored.row() == 22
    assert abs(int(view.visualRect(restored).center().x()) - anchor_x) <= 1


def test_unresolved_scan_anchor_preserves_scroll_without_selecting_stale_row(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(40)]
    current_path = paths[12]
    model = _AssetListModel(paths, current_path)
    proxy = SpacerProxyModel()
    proxy.setSourceModel(model)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(proxy)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    selected = proxy.mapFromSource(model.index(12, 0))
    view.selectionModel().setCurrentIndex(selected, QItemSelectionModel.ClearAndSelect)
    view.center_on_index(selected)
    qapp.processEvents()
    scroll_value = view.horizontalScrollBar().value()
    without_current = [path for path in paths if path != current_path]
    without_current.insert(0, Path("/library/new-photo.jpg"))
    model.publish_scan_revision(without_current)
    qapp.processEvents()

    restored = view.selectionModel().currentIndex()
    assert not restored.isValid()
    assert view.horizontalScrollBar().value() == scroll_value
    assert model.index(12, 0).data(Roles.ABS) != str(current_path)
