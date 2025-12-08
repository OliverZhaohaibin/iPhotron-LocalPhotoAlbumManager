
import pytest
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

# Skip if PySide6 not available
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
except ImportError:
    pytest.skip("PySide6 not installed", allow_module_level=True)

from src.iPhoto.gui.ui.tasks.asset_loader_worker import _parse_timestamp, build_asset_entry
from src.iPhoto.gui.ui.models.roles import Roles
from src.iPhoto.gui.ui.models.proxy_filter import AssetFilterProxyModel
from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_parse_timestamp():
    # Valid ISO string
    assert _parse_timestamp("2023-10-27T10:00:00Z") == datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc).timestamp()

    # Valid ISO string without Z
    dt = datetime(2023, 10, 27, 10, 0, 0)
    expected = dt.replace(tzinfo=timezone.utc).timestamp()
    assert _parse_timestamp("2023-10-27T10:00:00") == expected

    # Float/Int
    assert _parse_timestamp(1698393600.0) == 1698393600.0
    assert _parse_timestamp(1698393600) == 1698393600.0

    # datetime object with timezone
    dt_obj = datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc)
    assert _parse_timestamp(dt_obj) == dt_obj.timestamp()

    # datetime object without timezone (should get UTC assigned)
    dt_no_tz = datetime(2023, 10, 27, 10, 0, 0)
    expected_ts = dt_no_tz.replace(tzinfo=timezone.utc).timestamp()
    assert _parse_timestamp(dt_no_tz) == expected_ts

    # Invalid/Empty
    assert _parse_timestamp("") == float("-inf")
    assert _parse_timestamp(None) == float("-inf")
    assert _parse_timestamp("invalid-date") == float("-inf")

def test_build_asset_entry_includes_dt_sort(tmp_path):
    root = tmp_path / "album"
    root.mkdir()

    row = {
        "rel": "photo.jpg",
        "dt": "2023-10-27T10:00:00Z",
        "w": 100, "h": 100, "bytes": 1000
    }

    entry = build_asset_entry(
        root,
        row,
        featured=set(),
        live_map={},
        hidden_motion_paths=set()
    )

    assert "dt_sort" in entry
    assert entry["dt_sort"] == datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc).timestamp()

def test_asset_list_model_data_dt_sort(qapp):
    # Mock facade and dependencies
    facade = MagicMock()
    model = AssetListModel(facade)

    # Inject a row directly into state manager for testing
    row_data = {
        "rel": "test.jpg",
        "dt": "2023-10-27T10:00:00Z",
        "dt_sort": 1234567890.0
    }

    model._state_manager.set_rows([row_data])

    index = model.index(0, 0)
    assert model.data(index, Roles.DT_SORT) == 1234567890.0

    # Test fallback
    row_data_no_sort = {
        "rel": "test2.jpg",
        "dt": None
    }
    model._state_manager.set_rows([row_data_no_sort])
    index = model.index(0, 0)
    assert model.data(index, Roles.DT_SORT) == float("-inf")

def test_proxy_sort_order(qapp):
    # Setup source model
    facade = MagicMock()
    source_model = AssetListModel(facade)

    # Ensure role names are populated so proxy can use them if needed (though it uses Roles enum)
    # The key part is that the source model must return data for DT_SORT

    rows = [
        {"rel": "a.jpg", "dt_sort": 100.0},
        {"rel": "b.jpg", "dt_sort": 200.0},
        {"rel": "c.jpg", "dt_sort": float("-inf")}, # No date
        {"rel": "d.jpg", "dt_sort": 50.0},
        {"rel": "e.jpg", "dt_sort": 0.0},  # Unix epoch
    ]
    source_model._state_manager.set_rows(rows)

    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(source_model)
    proxy.setSortRole(Roles.DT) # This should trigger the custom lessThan using DT_SORT

    # Test Ascending
    proxy.sort(0, Qt.SortOrder.AscendingOrder)

    # Expected order: c (-inf), e (0.0), d (50), a (100), b (200)
    assert proxy.data(proxy.index(0, 0), Roles.REL) == "c.jpg"
    assert proxy.data(proxy.index(1, 0), Roles.REL) == "e.jpg"
    assert proxy.data(proxy.index(2, 0), Roles.REL) == "d.jpg"
    assert proxy.data(proxy.index(3, 0), Roles.REL) == "a.jpg"
    assert proxy.data(proxy.index(4, 0), Roles.REL) == "b.jpg"

    # Test Descending
    proxy.sort(0, Qt.SortOrder.DescendingOrder)

    # Expected order: b (200), a (100), d (50), e (0.0), c (-inf)
    assert proxy.data(proxy.index(0, 0), Roles.REL) == "b.jpg"
    assert proxy.data(proxy.index(1, 0), Roles.REL) == "a.jpg"
    assert proxy.data(proxy.index(2, 0), Roles.REL) == "d.jpg"
    assert proxy.data(proxy.index(3, 0), Roles.REL) == "e.jpg"
    assert proxy.data(proxy.index(4, 0), Roles.REL) == "c.jpg"
