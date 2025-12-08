
import pytest
import os
from unittest.mock import MagicMock

try:
    from PySide6.QtCore import Qt, QModelIndex
    from PySide6.QtWidgets import QApplication
except ImportError:
    pytest.skip("PySide6 not installed", allow_module_level=True)

from src.iPhoto.gui.ui.models.proxy_filter import AssetFilterProxyModel
from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel
from src.iPhoto.gui.ui.models.roles import Roles

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

class MockAssetListModel(AssetListModel):
    def __init__(self, rows):
        # Bypass super init to avoid complex dependencies if possible,
        # or use MagicMock for facade.
        facade = MagicMock()
        super().__init__(facade)
        self._state_manager.set_rows(rows)

def test_filter_early_exit(qapp):
    """
    Verify that when no filters/search are active, filterAcceptsRow returns True
    potentially bypassing deep logic.
    """
    rows = [
        {"rel": "a.jpg", "ts": 1, "is_video": False, "is_live": False, "featured": False, "id": "a"},
    ]
    model = MockAssetListModel(rows)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    # By default, filter mode is None and search text is empty.
    assert proxy.filter_mode() is None
    assert proxy.search_text() == ""

    # Should accept without looking deeply (conceptually).
    # Practically we verify it accepts.
    assert proxy.filterAcceptsRow(0, QModelIndex()) is True

def test_filter_direct_access_optimization(qapp):
    """
    Verify filtering logic still works with the new direct access implementation.
    """
    rows = [
        {"rel": "vid.mp4", "ts": 1, "is_video": True, "is_live": False, "featured": False, "id": "vid"},
        {"rel": "img.jpg", "ts": 2, "is_video": False, "is_live": False, "featured": True, "id": "img"},
        {"rel": "live.jpg", "ts": 3, "is_video": False, "is_live": True, "featured": False, "id": "live"},
    ]
    model = MockAssetListModel(rows)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    # Filter Videos
    proxy.set_filter_mode("videos")
    assert proxy.filterAcceptsRow(0, QModelIndex()) is True
    assert proxy.filterAcceptsRow(1, QModelIndex()) is False
    assert proxy.filterAcceptsRow(2, QModelIndex()) is False

    # Filter Favorites
    proxy.set_filter_mode("favorites")
    assert proxy.filterAcceptsRow(0, QModelIndex()) is False
    assert proxy.filterAcceptsRow(1, QModelIndex()) is True
    assert proxy.filterAcceptsRow(2, QModelIndex()) is False

    # Filter Live
    proxy.set_filter_mode("live")
    assert proxy.filterAcceptsRow(0, QModelIndex()) is False
    assert proxy.filterAcceptsRow(1, QModelIndex()) is False
    assert proxy.filterAcceptsRow(2, QModelIndex()) is True

    # Search Text
    proxy.set_filter_mode(None)
    proxy.set_search_text("vid")
    assert proxy.filterAcceptsRow(0, QModelIndex()) is True
    assert proxy.filterAcceptsRow(1, QModelIndex()) is False
    assert proxy.filterAcceptsRow(2, QModelIndex()) is False

def test_sort_lazy_evaluation(qapp):
    """
    Verify that sorting works correctly, specifically the timestamp collision handling
    where it falls back to string comparison of 'rel'.
    """
    rows = [
        {"rel": "b.jpg", "ts": 100, "is_video": False, "is_live": False, "featured": False, "id": "b"},
        {"rel": "a.jpg", "ts": 100, "is_video": False, "is_live": False, "featured": False, "id": "a"},
        {"rel": "c.jpg", "ts": 200, "is_video": False, "is_live": False, "featured": False, "id": "c"},
    ]
    model = MockAssetListModel(rows)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.setSortRole(Roles.DT)

    # Sort Ascending
    proxy.sort(0, Qt.SortOrder.AscendingOrder)

    # Expected: a.jpg (100), b.jpg (100), c.jpg (200)
    # Because timestamps 100 are equal, 'a.jpg' < 'b.jpg'

    assert proxy.data(proxy.index(0, 0), Roles.REL) == "a.jpg"
    assert proxy.data(proxy.index(1, 0), Roles.REL) == "b.jpg"
    assert proxy.data(proxy.index(2, 0), Roles.REL) == "c.jpg"

    # Sort Descending
    proxy.sort(0, Qt.SortOrder.DescendingOrder)

    # Expected: c.jpg (200), b.jpg (100), a.jpg (100)
    # In descending order, items with equal timestamps maintain reverse alphabetical order

    assert proxy.data(proxy.index(0, 0), Roles.REL) == "c.jpg"
    assert proxy.data(proxy.index(1, 0), Roles.REL) == "b.jpg"
    assert proxy.data(proxy.index(2, 0), Roles.REL) == "a.jpg"

def test_missing_keys_raises_key_error(qapp):
    """
    Verify that we are indeed using direct access by ensuring it fails when keys are missing.
    This confirms the optimization is active.

    NOTE: This is a synthetic test case. In production, `AssetLoader` guarantees that
    keys like `is_video`, `ts`, etc., are always present. This test intentionally violates
    that guarantee to prove that the code is using fast direct access (`row['key']`)
    instead of safer but slower `.get('key')`.
    """
    # Row missing required keys (is_video, is_live, featured, id)
    rows = [
        {"rel": "bad.jpg", "ts": 100} # Missing other keys
    ]
    model = MockAssetListModel(rows)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    # Should not raise yet because filter is None (Optimization 1: Early Exit)
    try:
        proxy.filterAcceptsRow(0, QModelIndex())
    except KeyError:
        pytest.fail("Should have early exited before accessing keys")

    # Enable a filter to force key access
    # QSortFilterProxyModel.invalidateFilter (called by set_filter_mode) triggers re-filtering immediately.
    with pytest.raises(KeyError):
        proxy.set_filter_mode("videos")
