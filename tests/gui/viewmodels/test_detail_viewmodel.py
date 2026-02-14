"""Tests for DetailViewModel — pure Python, no Qt dependency."""

from unittest.mock import Mock

from iPhoto.events.bus import EventBus
from iPhoto.gui.viewmodels.detail_viewmodel import DetailViewModel


def _make_vm():
    asset_service = Mock()
    bus = EventBus()
    vm = DetailViewModel(asset_service=asset_service, event_bus=bus)
    return vm, asset_service, bus


class TestDetailViewModel:
    def test_load_asset(self):
        vm, svc, _ = _make_vm()
        asset = Mock(id="a1", is_favorite=True, metadata={"key": "val"})
        svc.get_asset.return_value = asset

        vm.load_asset("a1")

        assert vm.current_asset.value is asset
        assert vm.is_favorite.value is True
        assert vm.metadata.value == {"key": "val"}
        assert vm.loading.value is False

    def test_load_asset_not_found(self):
        vm, svc, _ = _make_vm()
        svc.get_asset.return_value = None
        errors = []
        vm.error_occurred.connect(lambda msg: errors.append(msg))

        vm.load_asset("missing")

        assert len(errors) == 1
        assert "not found" in errors[0].lower()

    def test_load_asset_error(self):
        vm, svc, _ = _make_vm()
        svc.get_asset.side_effect = RuntimeError("db error")
        errors = []
        vm.error_occurred.connect(lambda msg: errors.append(msg))

        vm.load_asset("a1")

        assert len(errors) == 1
        assert "db error" in errors[0]
        assert vm.loading.value is False

    def test_toggle_favorite(self):
        vm, svc, _ = _make_vm()
        asset = Mock(id="a1", is_favorite=False, metadata={})
        svc.get_asset.return_value = asset
        svc.toggle_favorite.return_value = True
        vm.load_asset("a1")

        toggled = []
        vm.favorite_toggled.connect(lambda v: toggled.append(v))
        vm.toggle_favorite()

        assert vm.is_favorite.value is True
        assert toggled == [True]

    def test_toggle_favorite_no_asset(self):
        vm, svc, _ = _make_vm()
        vm.toggle_favorite()  # should not raise
        svc.toggle_favorite.assert_not_called()

    def test_update_metadata(self):
        vm, svc, _ = _make_vm()
        asset = Mock(id="a1", is_favorite=False, metadata={"a": 1})
        svc.get_asset.return_value = asset
        vm.load_asset("a1")

        vm.update_metadata({"b": 2})

        svc.update_metadata.assert_called_once_with("a1", {"b": 2})
        assert vm.metadata.value == {"a": 1, "b": 2}

    def test_update_metadata_no_asset(self):
        vm, svc, _ = _make_vm()
        vm.update_metadata({"b": 2})  # should not raise
        svc.update_metadata.assert_not_called()

    def test_set_editing(self):
        vm, _, _ = _make_vm()
        assert vm.editing.value is False
        vm.set_editing(True)
        assert vm.editing.value is True

    def test_clear_resets_state(self):
        vm, svc, _ = _make_vm()
        asset = Mock(id="a1", is_favorite=True, metadata={"x": 1})
        svc.get_asset.return_value = asset
        vm.load_asset("a1")
        vm.set_editing(True)

        vm.clear()

        assert vm.current_asset.value is None
        assert vm.metadata.value == {}
        assert vm.is_favorite.value is False
        assert vm.editing.value is False

    def test_asset_changed_signal(self):
        vm, svc, _ = _make_vm()
        asset = Mock(id="a1", is_favorite=False, metadata={})
        svc.get_asset.return_value = asset
        received = []
        vm.asset_changed.connect(lambda a: received.append(a))

        vm.load_asset("a1")

        assert len(received) == 1
        assert received[0] is asset

    def test_dispose(self):
        vm, _, bus = _make_vm()
        vm.dispose()
        assert len(vm._subscriptions) == 0
