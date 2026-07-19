from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from iPhoto.gui.detail_pipeline import DetailPrefetchDescriptor
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhoto.gui.ui.widgets.asset_grid import AssetGrid
from iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView


class _PrefetchModel(QStandardItemModel):
    def detail_prefetch_descriptor(self, row: int) -> DetailPrefetchDescriptor | None:
        if row < 0 or row >= self.rowCount():
            return None
        return DetailPrefetchDescriptor(
            row=row,
            asset_id=f"asset-{row}",
            path=Path(f"/virtual/photo-{row}.jpg"),
            is_video=False,
        )


def _single_item_prefetch_view(qapp_instance) -> tuple[GalleryGridView, _PrefetchModel]:
    view = GalleryGridView()
    model = _PrefetchModel()
    model.appendRow(QStandardItem("photo"))
    view.setModel(model)
    view.resize(400, 400)
    view.show()
    qapp_instance.processEvents()
    view.doItemsLayout()
    qapp_instance.processEvents()
    return view, model


# Attempt to patch load_icon in asset_delegate if it exists
def patch_delegate_icons(monkeypatch):
    # AssetGridDelegate doesn't use load_icon anymore, so this patch is likely obsolete.
    # We'll wrap it in try-except to avoid breaking tests if the import path is invalid.
    try:
        from PySide6.QtGui import QIcon
        def mock_load_icon(*args, **kwargs):
            return QIcon()

        # Patch where it is used. AssetGridDelegate imports it as `from ..icons import load_icon`
        monkeypatch.setattr("iPhoto.gui.ui.widgets.asset_delegate.load_icon", mock_load_icon)
    except (ImportError, AttributeError) as e:
        print(f"patch_delegate_icons: Could not patch load_icon: {e}")


@pytest.fixture
def qapp_instance():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    existing_widgets = tuple(app.topLevelWidgets())
    try:
        yield app
    finally:
        for widget in app.topLevelWidgets():
            if any(widget is existing for existing in existing_widgets):
                continue
            if isinstance(widget, GalleryGridView):
                widget.setModel(None)
            widget.close()
            widget.deleteLater()
        app.processEvents()


def test_gallery_responsive_layout(qapp_instance, monkeypatch):
    patch_delegate_icons(monkeypatch)

    # Setup view
    view = GalleryGridView()
    assert view.viewport().autoFillBackground()
    assert not view.viewport().testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
    assert not view.viewport().testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not view.viewport().testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
    delegate = AssetGridDelegate(view)
    view.setItemDelegate(delegate)

    model = QStandardItemModel()
    for _ in range(12):
        item = QStandardItem()
        item.setData(False, Roles.IS_SPACER)
        pix = QPixmap(100, 100)
        pix.fill(Qt.red)
        item.setData(pix, Qt.DecorationRole)
        model.appendRow(item)

    view.setModel(model)
    view.show()

    # Helper to calculate expectation
    def get_expectations(viewport_w):
        min_w = GalleryGridView.MIN_ITEM_WIDTH
        gap = GalleryGridView.ITEM_GAP
        # Use the safety margin from the implementation
        safety = GalleryGridView.SAFETY_MARGIN
        # Code uses safety margin for column count too (Bug fix)
        avail = viewport_w - safety
        cols = max(1, int(avail / (min_w + gap)))
        # Code uses safety margin for cell size calculation
        cell = int(avail / cols)
        item = cell - gap
        return cols, cell, item

    # -------------------------------------------------------------------------
    # Test Case 1: Standard scaling
    # -------------------------------------------------------------------------
    view.resize(800, 1200)
    qapp_instance.processEvents()
    view.doItemsLayout()
    qapp_instance.processEvents()

    vp_w = view.viewport().width()
    cols, cell, item = get_expectations(vp_w)

    assert view.gridSize().width() == cell
    assert view.iconSize().width() == item
    assert delegate._base_size == item

    # Check gap is strictly 2px
    r0 = view.visualRect(model.index(0, 0))
    r1 = view.visualRect(model.index(1, 0))
    gap = r1.x() - (r0.x() + r0.width())
    assert gap == 2

    # -------------------------------------------------------------------------
    # Test Case 2: Edge case handling (prevent column drop)
    # Width 784.
    # -------------------------------------------------------------------------
    view.resize(784, 1200)
    qapp_instance.processEvents()
    view.doItemsLayout()
    qapp_instance.processEvents()

    vp_w = view.viewport().width()
    cols, cell, item = get_expectations(vp_w)

    assert view.gridSize().width() == cell

    # Verify no wrap (all items up to `cols` are on first row)
    # index is 0-based. items 0 to cols-1 should be on row 0.
    last_item_idx = cols - 1
    r_last = view.visualRect(model.index(last_item_idx, 0))
    r0 = view.visualRect(model.index(0, 0))
    assert r_last.y() == r0.y()

    # -------------------------------------------------------------------------
    # Test Case 3: Expanding back to more columns
    # -------------------------------------------------------------------------
    view.resize(790, 1200)
    qapp_instance.processEvents()
    view.doItemsLayout()
    qapp_instance.processEvents()

    vp_w = view.viewport().width()
    cols, cell, item = get_expectations(vp_w)

    assert view.gridSize().width() == cell

    last_item_idx = cols - 1
    r_last = view.visualRect(model.index(last_item_idx, 0))
    r0 = view.visualRect(model.index(0, 0))
    assert r_last.y() == r0.y()

    # -------------------------------------------------------------------------
    # Test Case 4: Dead Zone check (Bug Fix Verification)
    # Width 582px triggers the dead zone where:
    #   Old logic: 582 / 194 = 3 cols. (582-10)/3 = 190.6 < 192. Reject.
    #   New logic: (582-10) / 194 = 2 cols. (582-10)/2 = 286. Item 284. Accept.
    # -------------------------------------------------------------------------
    view.resize(582, 1200)
    qapp_instance.processEvents()
    view.doItemsLayout()
    qapp_instance.processEvents()

    vp_w = view.viewport().width()
    cols, cell, item = get_expectations(vp_w)

    # Assert that we DO get an update (item size matches expectation)
    # If the bug were present, the item size would remain from previous step (Test Case 3)
    # Test Case 3 ended with ~790px -> 4 cols.
    # If update rejected, we would still have 4 cols logic on 582px? No, QListView would reflow.
    # But GridSize would be from Test Case 3 (approx 196px).
    # New expectation is 2 cols -> Cell ~288px.

    assert cols == 2
    assert view.gridSize().width() == cell
    assert view.iconSize().width() == item


def test_gallery_viewport_renders_an_opaque_background(qapp_instance):
    view = GalleryGridView()
    view.resize(640, 480)
    view.show()
    qapp_instance.processEvents()

    viewport = view.viewport()
    image = QImage(viewport.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        viewport.render(painter, QPoint())
    finally:
        painter.end()

    center = image.pixelColor(image.width() // 2, image.height() // 2)
    assert center.alpha() == 255
    view.close()


def test_delegate_assigned_after_show_uses_responsive_tile_size(qapp_instance, monkeypatch):
    patch_delegate_icons(monkeypatch)

    view = GalleryGridView()
    model = QStandardItemModel()
    for _ in range(6):
        item = QStandardItem()
        item.setData(False, Roles.IS_SPACER)
        pix = QPixmap(100, 100)
        pix.fill(Qt.red)
        item.setData(pix, Qt.DecorationRole)
        model.appendRow(item)

    view.resize(1200, 720)
    view.show()
    qapp_instance.processEvents()

    view.setModel(model)
    delegate = AssetGridDelegate(view)
    view.setItemDelegate(delegate)
    qapp_instance.processEvents()

    assert view.iconSize().width() != GalleryGridView.MIN_ITEM_WIDTH
    assert delegate._base_size == view.iconSize().width()

    first_rect = view.visualRect(model.index(0, 0))
    assert first_rect.width() == view.iconSize().width()


def test_favorite_badge_click_uses_viewport_coordinates(qapp_instance, monkeypatch):
    patch_delegate_icons(monkeypatch)

    view = GalleryGridView()
    delegate = AssetGridDelegate(view)
    view.setItemDelegate(delegate)

    model = QStandardItemModel()
    item = QStandardItem()
    item.setData(False, Roles.IS_SPACER)
    item.setData(True, Roles.FEATURED)
    pix = QPixmap(100, 100)
    pix.fill(Qt.red)
    item.setData(pix, Qt.DecorationRole)
    model.appendRow(item)

    view.setModel(model)
    view.resize(400, 400)
    view.show()
    qapp_instance.processEvents()
    view.doItemsLayout()
    qapp_instance.processEvents()

    index = model.index(0, 0)
    rect = view.visualRect(index)
    badge_pos = QPoint(rect.left() + 16, rect.bottom() - 16)
    badge_global = view.viewport().mapToGlobal(badge_pos)

    class FakeMouseEvent:
        def button(self):
            return Qt.MouseButton.LeftButton

        def position(self):
            return QPointF(-999.0, -999.0)

        def pos(self):
            return QPoint(-999, -999)

        def globalPosition(self):
            return QPointF(float(badge_global.x()), float(badge_global.y()))

    monkeypatch.setattr(AssetGrid, "mousePressEvent", lambda self, event: None)

    spy = QSignalSpy(view.favoriteClicked)
    view.mousePressEvent(FakeMouseEvent())

    assert spy.count() == 1
    assert spy.at(0)[0].row() == 0


def test_mouse_press_does_not_emit_detail_prefetch(qapp_instance):
    view, model = _single_item_prefetch_view(qapp_instance)
    spy = QSignalSpy(view.detailPrefetchRequested)
    index = model.index(0, 0)

    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        view.visualRect(index).center(),
    )
    qapp_instance.processEvents()

    assert spy.count() == 0
    view.close()


def test_hover_dwell_emits_one_full_descriptor_after_150ms(qapp_instance):
    view, model = _single_item_prefetch_view(qapp_instance)
    spy = QSignalSpy(view.detailPrefetchRequested)

    assert view._detail_prefetch_timer.interval() == 150
    view._schedule_detail_prefetch(model.index(0, 0))
    QTest.qWait(175)
    qapp_instance.processEvents()
    QTest.qWait(50)

    assert spy.count() == 1
    descriptor = spy.at(0)[0]
    assert descriptor.asset_id == "asset-0"
    assert descriptor.path == Path("/virtual/photo-0.jpg")
    view.close()


def test_selection_mode_and_model_reset_cancel_pending_hover_prefetch(qapp_instance):
    view, model = _single_item_prefetch_view(qapp_instance)
    spy = QSignalSpy(view.detailPrefetchRequested)

    view._schedule_detail_prefetch(model.index(0, 0))
    view.set_selection_mode_enabled(True)
    QTest.qWait(175)
    assert spy.count() == 0

    view.set_selection_mode_enabled(False)
    view._schedule_detail_prefetch(model.index(0, 0))
    model.clear()
    QTest.qWait(175)
    assert spy.count() == 0
    view.close()
