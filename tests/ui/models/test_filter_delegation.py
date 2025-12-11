from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt

# Stub for QAbstractItemModel since we just need the type compatibility
class MockSourceModel(QAbstractItemModel):
    def __init__(self):
        super().__init__()
        self.filter_mode = None

    def set_filter_mode(self, mode):
        self.filter_mode = mode

    # Implement abstract methods to satisfy QAbstractItemModel (though not used in this test)
    def index(self, row, column, parent=QModelIndex()):
        return QModelIndex()
    def parent(self, child):
        return QModelIndex()
    def rowCount(self, parent=QModelIndex()):
        return 0
    def columnCount(self, parent=QModelIndex()):
        return 0
    def data(self, index, role=Qt.DisplayRole):
        return None

from src.iPhoto.gui.ui.models.proxy_filter import AssetFilterProxyModel

def test_proxy_delegates_filter_mode() -> None:
    """Test that setting filter mode on proxy calls the source model."""
    proxy = AssetFilterProxyModel()
    source = MockSourceModel()
    proxy.setSourceModel(source)

    proxy.set_filter_mode("videos")
    assert source.filter_mode == "videos"

    proxy.set_filter_mode(None)
    assert source.filter_mode is None

def test_proxy_delegates_only_if_supported() -> None:
    """Test that proxy handles source models without set_filter_mode gracefully."""
    proxy = AssetFilterProxyModel()

    class DumbModel(QAbstractItemModel):
        def index(self, row, column, parent=QModelIndex()): return QModelIndex()
        def parent(self, child): return QModelIndex()
        def rowCount(self, parent=QModelIndex()): return 0
        def columnCount(self, parent=QModelIndex()): return 0
        def data(self, index, role=Qt.DisplayRole): return None

    source = DumbModel()
    # verify it doesn't have set_filter_mode
    assert not hasattr(source, "set_filter_mode")

    proxy.setSourceModel(source)
    # Should not raise AttributeError
    proxy.set_filter_mode("videos")
