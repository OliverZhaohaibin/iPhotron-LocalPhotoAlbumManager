from pathlib import Path

import pytest
from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelectionModel,
    QModelIndex,
    QSize,
    QStringListModel,
    Qt,
)
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.models.spacer_proxy_model import SpacerProxyModel
from iPhoto.gui.ui.models.thumbnail_surface_proxy_model import ThumbnailSurfaceProxyModel
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

    def set_current_path(self, path: Path) -> None:
        old_row = self._paths.index(self._current_path)
        new_row = self._paths.index(path)
        self._current_path = path
        roles = [Roles.IS_CURRENT, Qt.ItemDataRole.SizeHintRole]
        self.dataChanged.emit(self.index(old_row, 0), self.index(old_row, 0), roles)
        self.dataChanged.emit(self.index(new_row, 0), self.index(new_row, 0), roles)


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


def test_playback_selection_cancels_queued_show_restore_before_centering(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(40)]
    model = _AssetListModel(paths, paths[5])
    proxy = SpacerProxyModel()
    proxy.setSourceModel(model)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(proxy)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    stale = proxy.mapFromSource(model.index(5, 0))
    target = proxy.mapFromSource(model.index(24, 0))
    view.selectionModel().setCurrentIndex(
        stale,
        QItemSelectionModel.ClearAndSelect,
    )
    view.center_on_index(stale)
    view._capture_scroll_state()
    stale_scroll_value = view.horizontalScrollBar().value()
    view._schedule_restore_scroll("show")
    assert view._restore_timer.isActive()

    assert view.select_index_for_centering(target) is True

    assert not view._restore_timer.isActive()
    assert view._pending_scroll_value is None
    view.center_on_index(target)
    target_scroll_value = view.horizontalScrollBar().value()
    qapp.processEvents()

    assert view.selectionModel().currentIndex() == target
    assert view.horizontalScrollBar().value() == target_scroll_value
    assert target_scroll_value != stale_scroll_value


def test_centering_settles_current_tile_geometry_before_scroll(qapp):
    """Model Windows processing a delayed item layout after the center request."""

    paths = [Path(f"/library/photo-{index}.jpg") for index in range(40)]
    model = _AssetListModel(paths, paths[5])
    proxy = SpacerProxyModel()
    proxy.setSourceModel(model)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(proxy)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    initial = proxy.mapFromSource(model.index(5, 0))
    target = proxy.mapFromSource(model.index(24, 0))
    view.select_index_for_centering(initial)
    view.center_on_index(initial)
    qapp.processEvents()

    model.set_current_path(paths[24])
    view.select_index_for_centering(target)
    view.center_on_index(target)
    centered_x = int(view.visualRect(target).center().x())

    view.executeDelayedItemsLayout()
    qapp.processEvents()

    assert abs(int(view.visualRect(target).center().x()) - centered_x) <= 1


def test_horizontal_viewport_demand_maps_through_two_proxies(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(1_200)]
    source = _AssetListModel(paths, paths[10])
    presentation = ThumbnailSurfaceProxyModel("filmstrip")
    presentation.setSourceModel(source)
    spacers = SpacerProxyModel()
    spacers.setSourceModel(presentation)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(spacers)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    emitted = []
    view.viewportStateChanged.connect(emitted.append)
    target_source = source.index(1_000, 0)
    target = spacers.mapFromSource(presentation.mapFromSource(target_source))
    view.select_index_for_centering(target)
    view.center_on_index(target)
    qapp.processEvents()

    assert emitted
    demand = emitted[-1]
    assert demand.surface_id == "filmstrip"
    assert demand.visible_first <= 1_000 <= demand.visible_last
    assert demand.visible_first > 900
    assert demand.display_bucket == 512

    view._scroll_controller._publish_idle_state()
    view._emit_visible_rows()
    settled = emitted[-1]
    assert settled.phase == "settled"
    assert settled.full_prefetch_range == settled.full_guard_range
    assert tuple(settled.iter_full_speculative_rows()) == ()


def test_filmstrip_geometry_is_independent_from_thumbnail_bucket(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(3)]
    source = _AssetListModel(paths, paths[1])
    view = FilmstripView()
    delegate = AssetGridDelegate(view, filmstrip_mode=True)
    view.setItemDelegate(delegate)
    view.setModel(source)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    option = QStyleOptionViewItem()
    option.initFrom(view)
    assert view.iconSize() == QSize(120, 120)
    assert view.spacing() == 2
    assert view.minimumHeight() == 132
    assert view.maximumHeight() == 132
    assert delegate.sizeHint(option, source.index(1, 0)) == QSize(120, 120)
    assert delegate.sizeHint(option, source.index(0, 0)) == QSize(72, 120)


def test_hidden_filmstrip_cancels_delayed_viewport_publication(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(80)]
    source = _AssetListModel(paths, paths[10])
    presentation = ThumbnailSurfaceProxyModel("filmstrip")
    presentation.setSourceModel(source)
    spacers = SpacerProxyModel()
    spacers.setSourceModel(presentation)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(spacers)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    emitted = []
    view.viewportStateChanged.connect(emitted.append)
    view.schedule_viewport_publish()
    view._scroll_controller._idle_timer.start(0)
    view._scroll_controller._dwell_timer.start(0)
    view._scroll_controller._direction_expiry_timer.start(0)
    view.hide()
    qapp.processEvents()
    view._emit_visible_rows()

    assert emitted == []
    assert not view._update_timer.isActive()
    assert not view._scroll_controller._idle_timer.isActive()
    assert not view._scroll_controller._dwell_timer.isActive()
    assert not view._scroll_controller._direction_expiry_timer.isActive()


def test_filmstrip_demand_bounds_use_source_count_without_spacers(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(1_200)]
    source = _AssetListModel(paths, paths[-1])
    presentation = ThumbnailSurfaceProxyModel("filmstrip")
    presentation.setSourceModel(source)
    spacers = SpacerProxyModel()
    spacers.setSourceModel(presentation)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(spacers)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    emitted = []
    view.viewportStateChanged.connect(emitted.append)
    source_index = source.index(1_199, 0)
    target = spacers.mapFromSource(presentation.mapFromSource(source_index))
    view.select_index_for_centering(target)
    view.center_on_index(target)
    qapp.processEvents()

    assert emitted
    demand = emitted[-1]
    assert demand.visible_last <= 1_199
    assert demand.full_guard_last <= 1_199
    assert demand.full_prefetch_last <= 1_199
    assert demand.warm_last <= 1_199


def test_current_change_does_not_publish_transient_spacer_width(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(40)]
    model = _AssetListModel(paths, paths[5])
    proxy = SpacerProxyModel()
    proxy.setSourceModel(model)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(proxy)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()

    widths: list[int] = []
    original_set_spacer_width = proxy.set_spacer_width

    def record_spacer_width(width: int) -> None:
        widths.append(width)
        original_set_spacer_width(width)

    proxy.set_spacer_width = record_spacer_width  # type: ignore[method-assign]
    model.set_current_path(paths[24])
    target = proxy.mapFromSource(model.index(24, 0))
    view.select_index_for_centering(target)

    expected = max(0, (view.viewport().width() - 120) // 2)
    assert widths
    assert set(widths) == {expected}


def test_centering_executes_delayed_layout_before_reading_item_geometry(qapp):
    paths = [Path(f"/library/photo-{index}.jpg") for index in range(20)]
    model = _AssetListModel(paths, paths[5])
    proxy = SpacerProxyModel()
    proxy.setSourceModel(model)
    view = FilmstripView()
    view.setItemDelegate(AssetGridDelegate(view, filmstrip_mode=True))
    view.setModel(proxy)
    view.resize(420, 132)
    view.show()
    qapp.processEvents()
    target = proxy.mapFromSource(model.index(5, 0))

    calls: list[str] = []
    execute_layout = view.executeDelayedItemsLayout
    visual_rect = view.visualRect

    def record_layout() -> None:
        calls.append("layout")
        execute_layout()

    def record_rect(index: QModelIndex):
        calls.append("rect")
        return visual_rect(index)

    view.executeDelayedItemsLayout = record_layout  # type: ignore[method-assign]
    view.visualRect = record_rect  # type: ignore[method-assign]
    view.center_on_index(target)

    assert "layout" in calls
    assert "rect" in calls
    layout_position = calls.index("layout")
    assert "rect" in calls[layout_position + 1 :]


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
