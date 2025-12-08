
import pytest
from unittest.mock import MagicMock
from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel
from src.iPhoto.gui.ui.models.proxy_filter import AssetFilterProxyModel
from src.iPhoto.gui.ui.models.roles import Roles

# Skip if PySide6 not available
try:
    from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex
except ImportError:
    pytest.skip("PySide6 not installed", allow_module_level=True)

# -----------------------------------------------------------------------------
# 1. Test AssetListModel.get_internal_row
# -----------------------------------------------------------------------------

def test_get_internal_row_valid_and_bounds():
    facade = MagicMock()
    model = AssetListModel(facade)

    row1 = {"rel": "a.jpg", "id": "1", "dt": 100}
    row2 = {"rel": "b.jpg", "id": "2", "dt": 200}
    model._state_manager.set_rows([row1, row2])

    # 1. Valid row indices
    assert model.get_internal_row(0) is row1
    assert model.get_internal_row(1) is row2

    # 2. Out-of-bounds indices
    assert model.get_internal_row(-1) is None
    assert model.get_internal_row(2) is None
    assert model.get_internal_row(100) is None

def test_get_internal_row_matches_data():
    facade = MagicMock()
    model = AssetListModel(facade)

    row = {
        "rel": "test.jpg",
        "id": "123",
        "is_video": True,
        "featured": True
    }
    model._state_manager.set_rows([row])

    internal = model.get_internal_row(0)
    index = model.index(0, 0)

    assert internal["rel"] == model.data(index, Roles.REL)
    assert internal["id"] == model.data(index, Roles.ASSET_ID)
    assert internal["is_video"] == model.data(index, Roles.IS_VIDEO)
    assert internal["featured"] == model.data(index, Roles.FEATURED)

# -----------------------------------------------------------------------------
# 2. Test AssetFilterProxyModel.lessThan (Fast Path)
# -----------------------------------------------------------------------------

class MockFastSource(QAbstractItemModel):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def rowCount(self, parent=None):
        return len(self.rows)

    def columnCount(self, parent=None):
        return 1

    def index(self, row, column, parent=None):
        return self.createIndex(row, column)

    def parent(self, index):
        return QModelIndex()

    def data(self, index, role):
        # Fallback for standard path testing
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Roles.DT_SORT:
            return row.get("dt_sort")
        if role == Roles.REL:
            return row.get("rel")
        return None

    def get_internal_row(self, row):
        if 0 <= row < len(self.rows):
            return self.rows[row]
        return None

class MockStandardSource(QAbstractItemModel):
    """Same as MockFastSource but WITHOUT get_internal_row to force standard path."""
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def rowCount(self, parent=None):
        return len(self.rows)

    def columnCount(self, parent=None):
        return 1

    def index(self, row, column, parent=None):
        return self.createIndex(row, column)

    def parent(self, index):
        return QModelIndex()

    def data(self, index, role):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Roles.DT_SORT:
            return row.get("dt_sort")
        if role == Roles.REL:
            return row.get("rel")
        return None

def test_lessThan_fast_vs_standard_parity():
    rows = [
        {"rel": "a.jpg", "dt_sort": 100.0},
        {"rel": "b.jpg", "dt_sort": 200.0},
        {"rel": "c.jpg", "dt_sort": 100.0}, # Same time as a.jpg, tiebreaker needed
        {"rel": "d.jpg", "dt_sort": None},  # No date
    ]

    # Fast path setup
    fast_source = MockFastSource(rows)
    fast_proxy = AssetFilterProxyModel()
    fast_proxy.setSourceModel(fast_source)
    fast_proxy.setSortRole(Roles.DT)

    # Standard path setup
    std_source = MockStandardSource(rows)
    std_proxy = AssetFilterProxyModel()
    std_proxy.setSourceModel(std_source)
    std_proxy.setSortRole(Roles.DT)

    indices = [(i, j) for i in range(len(rows)) for j in range(len(rows)) if i != j]

    for i, j in indices:
        idx_fast_left = fast_source.index(i, 0)
        idx_fast_right = fast_source.index(j, 0)

        idx_std_left = std_source.index(i, 0)
        idx_std_right = std_source.index(j, 0)

        # Check that fast proxy actually has _fast_source
        assert fast_proxy._fast_source is not None
        # Check that standard proxy does NOT have _fast_source
        assert std_proxy._fast_source is None

        fast_result = fast_proxy.lessThan(idx_fast_left, idx_fast_right)
        std_result = std_proxy.lessThan(idx_std_left, idx_std_right)

        assert fast_result == std_result, f"Mismatch for rows {i} vs {j}"

def test_lessThan_fast_path_none_handling():
    # Test robustness when get_internal_row returns None
    rows = [{"rel": "a.jpg", "dt_sort": 100.0}]
    source = MockFastSource(rows)
    # Patch get_internal_row to return None for row 0
    source.get_internal_row = MagicMock(return_value=None)

    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(source)
    proxy.setSortRole(Roles.DT)

    left = source.index(0, 0)
    right = source.index(0, 0)

    # Should not crash, effectively comparing -inf with -inf
    result = proxy.lessThan(left, right)
    assert result is False # equal

# -----------------------------------------------------------------------------
# 3. Test AssetFilterProxyModel.filterAcceptsRow (Fast Path)
# -----------------------------------------------------------------------------

def test_filterAcceptsRow_fast_vs_standard_parity():
    rows = [
        {"rel": "vid.mp4", "is_video": True, "is_live": False, "featured": False, "id": "v1"},
        {"rel": "img.jpg", "is_video": False, "is_live": False, "featured": True, "id": "i1"},
        {"rel": "live.heic", "is_video": False, "is_live": True, "featured": False, "id": "l1"},
    ]

    # We need a MockStandardSource that implements data() for all filter roles
    class FullMockStandardSource(QAbstractItemModel):
        def __init__(self, rows):
            super().__init__()
            self.rows = rows
        def rowCount(self, parent=None): return len(self.rows)
        def columnCount(self, parent=None): return 1
        def index(self, row, column, parent=None): return self.createIndex(row, column)
        def parent(self, index): return QModelIndex()
        def data(self, index, role):
            if not index.isValid(): return None
            row = self.rows[index.row()]
            if role == Roles.IS_VIDEO: return row["is_video"]
            if role == Roles.IS_LIVE: return row["is_live"]
            if role == Roles.FEATURED: return row["featured"]
            if role == Roles.REL: return row["rel"]
            if role == Roles.ASSET_ID: return row["id"]
            return None

    class FullMockFastSource(FullMockStandardSource):
        def get_internal_row(self, row):
            if 0 <= row < len(self.rows): return self.rows[row]
            return None

    fast_source = FullMockFastSource(rows)
    std_source = FullMockStandardSource(rows)

    fast_proxy = AssetFilterProxyModel()
    fast_proxy.setSourceModel(fast_source)

    std_proxy = AssetFilterProxyModel()
    std_proxy.setSourceModel(std_source)

    assert fast_proxy._fast_source is not None
    assert std_proxy._fast_source is None

    filters = [
        ("videos", ""),
        ("live", ""),
        ("favorites", ""),
        (None, "vid"),
        (None, "i1"),
        (None, "nomatch")
    ]

    for mode, text in filters:
        fast_proxy.set_filters(mode=mode, text=text)
        std_proxy.set_filters(mode=mode, text=text)

        for i in range(len(rows)):
            fast_res = fast_proxy.filterAcceptsRow(i, QModelIndex())
            std_res = std_proxy.filterAcceptsRow(i, QModelIndex())
            assert fast_res == std_res, f"Mismatch for row {i} with mode={mode}, text={text}"

def test_filterAcceptsRow_none_handling():
    rows = [{"rel": "a.jpg"}]
    source = MockFastSource(rows)
    source.get_internal_row = MagicMock(return_value=None)

    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(source)

    # If row data is None, should return False (reject)
    assert proxy.filterAcceptsRow(0, QModelIndex()) is False
