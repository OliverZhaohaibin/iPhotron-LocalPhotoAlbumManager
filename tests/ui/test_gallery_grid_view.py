import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel, QPixmap
from PySide6.QtWidgets import QApplication

from iPhotos.src.iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from iPhotos.src.iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from iPhotos.src.iPhoto.gui.ui.models.roles import Roles

# Attempt to patch load_icon in asset_delegate if it exists
def patch_delegate_icons(monkeypatch):
    from PySide6.QtGui import QIcon
    def mock_load_icon(*args, **kwargs):
        return QIcon()

    # Patch where it is used. AssetGridDelegate imports it as `from ..icons import load_icon`
    monkeypatch.setattr("iPhotos.src.iPhoto.gui.ui.widgets.asset_delegate.load_icon", mock_load_icon)

@pytest.fixture(scope="module")
def qapp_instance():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_gallery_responsive_layout(qapp_instance, monkeypatch):
    patch_delegate_icons(monkeypatch)

    # Setup view
    view = GalleryGridView()
    delegate = AssetGridDelegate(view)
    view.setItemDelegate(delegate)

    model = QStandardItemModel()
    for i in range(12):
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
        # Code uses raw viewport width for column count
        cols = max(1, int(viewport_w / (min_w + gap)))
        # Code uses safety margin for cell size calculation
        avail = viewport_w - safety
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
