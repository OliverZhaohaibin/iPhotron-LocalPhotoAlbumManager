"""Tests for PureAssetListViewModel — pure Python, no Qt dependency."""

from unittest.mock import Mock, MagicMock
from dataclasses import dataclass

from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanCompletedEvent, AssetImportedEvent
from iPhoto.gui.viewmodels.pure_asset_list_viewmodel import PureAssetListViewModel


def _make_vm(assets=None):
    """Create a PureAssetListViewModel with mocked dependencies."""
    data_source = Mock()
    data_source.load_assets = Mock(return_value=assets or [])
    thumbnail_cache = Mock()
    thumbnail_cache.get = Mock(return_value=b"thumb")
    bus = EventBus()
    vm = PureAssetListViewModel(
        data_source=data_source,
        thumbnail_cache=thumbnail_cache,
        event_bus=bus,
    )
    return vm, data_source, thumbnail_cache, bus


class TestPureAssetListViewModel:
    def test_load_album_sets_assets(self):
        vm, ds, _, _ = _make_vm(["asset1", "asset2"])

        vm.load_album("album-1")

        assert vm.assets.value == ["asset1", "asset2"]
        assert vm.total_count.value == 2
        assert vm.loading.value is False

    def test_load_album_clears_selection(self):
        vm, ds, _, _ = _make_vm(["a", "b"])
        vm.selected_indices.value = [0]

        vm.load_album("album-1")

        assert vm.selected_indices.value == []

    def test_select_adds_index(self):
        vm, _, _, _ = _make_vm(["a", "b", "c"])
        vm.load_album("x")

        vm.select(1)
        vm.select(2)

        assert vm.selected_indices.value == [1, 2]

    def test_select_duplicate_ignored(self):
        vm, _, _, _ = _make_vm()
        vm.select(0)
        vm.select(0)

        assert vm.selected_indices.value == [0]

    def test_deselect(self):
        vm, _, _, _ = _make_vm()
        vm.select(0)
        vm.select(1)
        vm.deselect(0)

        assert vm.selected_indices.value == [1]

    def test_clear_selection(self):
        vm, _, _, _ = _make_vm()
        vm.select(0)
        vm.select(1)
        vm.clear_selection()

        assert vm.selected_indices.value == []

    def test_get_thumbnail(self):
        vm, _, tc, _ = _make_vm()
        tc.get.return_value = b"thumb-data"

        result = vm.get_thumbnail("asset-1")

        assert result == b"thumb-data"
        tc.get.assert_called_once_with("asset-1")

    def test_get_asset_in_range(self):
        vm, _, _, _ = _make_vm(["a", "b", "c"])
        vm.load_album("x")

        assert vm.get_asset(1) == "b"

    def test_get_asset_out_of_range(self):
        vm, _, _, _ = _make_vm(["a"])
        vm.load_album("x")

        assert vm.get_asset(5) is None
        assert vm.get_asset(-1) is None

    def test_scan_completed_reloads(self):
        vm, ds, _, bus = _make_vm(["a"])
        vm.load_album("album-1")
        ds.load_assets.reset_mock()
        ds.load_assets.return_value = ["a", "b"]

        bus.publish(ScanCompletedEvent(album_id="album-1", asset_count=2))

        assert vm.total_count.value == 2
        ds.load_assets.assert_called_once_with("album-1")

    def test_scan_completed_different_album_ignored(self):
        vm, ds, _, bus = _make_vm(["a"])
        vm.load_album("album-1")
        ds.load_assets.reset_mock()

        bus.publish(ScanCompletedEvent(album_id="other-album", asset_count=5))

        ds.load_assets.assert_not_called()

    def test_asset_imported_reloads(self):
        vm, ds, _, bus = _make_vm(["a"])
        vm.load_album("album-1")
        ds.load_assets.reset_mock()
        ds.load_assets.return_value = ["a", "b", "c"]

        bus.publish(AssetImportedEvent(album_id="album-1", asset_ids=["b", "c"]))

        assert vm.total_count.value == 3

    def test_loading_state_during_load(self):
        loading_states = []

        vm, ds, _, _ = _make_vm()
        vm.loading.changed.connect(lambda new, old: loading_states.append(new))

        vm.load_album("album-1")

        assert True in loading_states
        assert vm.loading.value is False

    def test_dispose_stops_event_handling(self):
        vm, ds, _, bus = _make_vm(["a"])
        vm.load_album("album-1")
        ds.load_assets.reset_mock()

        vm.dispose()
        bus.publish(ScanCompletedEvent(album_id="album-1", asset_count=2))

        ds.load_assets.assert_not_called()

    def test_selection_changed_signal(self):
        vm, _, _, _ = _make_vm()
        selections = []
        vm.selection_changed.connect(lambda s: selections.append(s))

        vm.select(0)
        vm.select(1)

        assert selections == [[0], [0, 1]]

    def test_error_handling(self):
        vm, ds, _, _ = _make_vm()
        ds.load_assets.side_effect = RuntimeError("boom")
        errors = []
        vm.error_occurred.connect(lambda msg: errors.append(msg))

        vm.load_album("fail-album")

        assert len(errors) == 1
        assert "boom" in errors[0]
        assert vm.loading.value is False
