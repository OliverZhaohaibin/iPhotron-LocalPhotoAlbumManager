"""Tests for ViewModelFactory — pure Python, no Qt dependency."""

from unittest.mock import Mock

from iPhoto.di.container import Container
from iPhoto.events.bus import EventBus
from iPhoto.gui.factories.viewmodel_factory import ViewModelFactory
from iPhoto.gui.viewmodels.pure_asset_list_viewmodel import PureAssetListViewModel
from iPhoto.gui.viewmodels.album_tree_viewmodel import AlbumTreeViewModel
from iPhoto.gui.viewmodels.detail_viewmodel import DetailViewModel


def _make_factory():
    container = Container()
    container.register_instance(EventBus, EventBus())
    return ViewModelFactory(container), container


class TestViewModelFactory:
    def test_create_asset_list_vm(self):
        factory, _ = _make_factory()
        vm = factory.create_asset_list_vm()

        assert isinstance(vm, PureAssetListViewModel)

    def test_create_asset_list_vm_with_deps(self):
        factory, _ = _make_factory()
        ds = Mock()
        ds.load_assets = Mock(return_value=["a"])
        tc = Mock()

        vm = factory.create_asset_list_vm(data_source=ds, thumbnail_cache=tc)

        assert isinstance(vm, PureAssetListViewModel)
        vm.load_album("test")
        assert vm.total_count.value == 1

    def test_create_album_tree_vm(self):
        factory, container = _make_factory()
        from iPhoto.application.services.album_service import AlbumService
        container.register_instance(AlbumService, Mock(spec=AlbumService))

        vm = factory.create_album_tree_vm()

        assert isinstance(vm, AlbumTreeViewModel)

    def test_create_detail_vm(self):
        factory, container = _make_factory()
        from iPhoto.application.services.asset_service import AssetService
        container.register_instance(AssetService, Mock(spec=AssetService))

        vm = factory.create_detail_vm()

        assert isinstance(vm, DetailViewModel)

    def test_factory_uses_same_event_bus(self):
        factory, container = _make_factory()
        bus = container.resolve(EventBus)

        vm = factory.create_asset_list_vm()
        # The VM should subscribe via the same EventBus
        assert len(vm._subscriptions) == 2  # ScanCompleted + AssetImported
