"""Test optimizations in AssetFilterProxyModel."""

from __future__ import annotations

import sys
import os

from PySide6.QtCore import QAbstractListModel, Qt, QModelIndex
from PySide6.QtWidgets import QApplication

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from iPhoto.gui.ui.models.proxy_filter import AssetFilterProxyModel

class MockSourceModel(QAbstractListModel):
    def __init__(self, count=100):
        super().__init__()
        self._count = count

    def rowCount(self, parent=QModelIndex()):
        return self._count

    def data(self, index, role=Qt.DisplayRole):
        return None

    def get_internal_row(self, row):
        return {
            "id": row,
            "ts": 1000 - row,
            "rel": f"img{row}",
            "is_video": False,
            "is_live": False,
            "featured": False
        }

def test_proxy_optimization_flag():
    """Verify that the optimization flag short-circuits sorting."""

    app = QApplication.instance() or QApplication()

    source = MockSourceModel(100)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(source)

    # Enable bypass
    proxy._bypass_sort_optimization = True

    # Create two indices
    idx0 = proxy.createIndex(0, 0)
    idx1 = proxy.createIndex(1, 0)

    # Sort Descending (Newest First)
    # Source 0 (Newest) vs Source 1 (Oldest)
    # We want 0 before 1.
    # lessThan(0, 1) should be False.
    # With optimization: 0 > 1 is False.
    assert proxy.lessThan(idx0, idx1) is False

    # lessThan(1, 0) should be True.
    # With optimization: 1 > 0 is True.
    assert proxy.lessThan(idx1, idx0) is True

    # Disable bypass and check standard behavior (which relies on get_internal_row)
    proxy._bypass_sort_optimization = False

    # Standard behavior compares timestamps
    # Row 0 TS=1000, Row 1 TS=999.
    # lessThan(0, 1) -> 1000 < 999 -> False.
    assert proxy.lessThan(idx0, idx1) is False
    assert proxy.lessThan(idx1, idx0) is True

def test_set_filter_mode_optimization():
    """Verify set_filter_mode triggers the optimization flag sequence."""

    QApplication.instance() or QApplication()

    proxy = AssetFilterProxyModel()
    source = MockSourceModel(100)
    proxy.setSourceModel(source)

    # We can't easily intercept the flag change during execution without subclassing or mocking.
    # But we can verify it returns to False.

    proxy.set_filter_mode("videos")
    assert proxy._bypass_sort_optimization is False

    # Verify dynamicSortFilter is NOT restored (per our decision)
    # Default is True.
    assert proxy.dynamicSortFilter() is False  # Because we disabled it and didn't restore

    # Reset it manually to test again
    proxy.setDynamicSortFilter(True)
    proxy.set_filter_mode(None)
    assert proxy.dynamicSortFilter() is False
