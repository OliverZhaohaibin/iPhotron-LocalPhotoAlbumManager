import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel, QPixmap
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView
from src.iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate
from src.iPhoto.gui.ui.models.roles import Roles

# Attempt to patch load_icon in asset_delegate if it exists
def patch_delegate_icons(monkeypatch):
    # AssetGridDelegate doesn't use load_icon anymore, so this patch is likely obsolete.
    # We'll wrap it in try-except to avoid breaking tests if the import path is invalid.
    try:
        from PySide6.QtGui import QIcon
        def mock_load_icon(*args, **kwargs):
            return QIcon()

        # Patch where it is used. AssetGridDelegate imports it as `from ..icons import load_icon`
        monkeypatch.setattr("src.iPhoto.gui.ui.widgets.asset_delegate.load_icon", mock_load_icon)
    except (ImportError, AttributeError) as e:
        print(f"patch_delegate_icons: Could not patch load_icon: {e}")

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
