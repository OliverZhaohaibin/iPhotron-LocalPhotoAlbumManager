"""ViewModelFactory — centralised ViewModel creation.

Replaces manual ViewModel instantiation scattered across Coordinators.
Uses the DI ``Container`` to resolve dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from iPhoto.di.container import Container
from iPhoto.events.bus import EventBus
from iPhoto.gui.viewmodels.album_tree_viewmodel import AlbumTreeViewModel
from iPhoto.gui.viewmodels.detail_viewmodel import DetailViewModel
from iPhoto.gui.viewmodels.pure_asset_list_viewmodel import PureAssetListViewModel

if TYPE_CHECKING:
    from iPhoto.application.services.album_service import AlbumService
    from iPhoto.application.services.asset_service import AssetService


class ViewModelFactory:
    """Centrally creates ViewModels — replaces manual creation in Coordinators."""

    def __init__(self, container: Container) -> None:
        self._container = container

    def create_asset_list_vm(
        self,
        data_source: object | None = None,
        thumbnail_cache: object | None = None,
    ) -> PureAssetListViewModel:
        event_bus = self._container.resolve(EventBus)
        return PureAssetListViewModel(
            data_source=data_source or _noop_data_source(),
            thumbnail_cache=thumbnail_cache or _noop_thumbnail_cache(),
            event_bus=event_bus,
        )

    def create_album_tree_vm(self) -> AlbumTreeViewModel:
        from iPhoto.application.services.album_service import AlbumService

        album_service = self._container.resolve(AlbumService)
        event_bus = self._container.resolve(EventBus)
        return AlbumTreeViewModel(
            album_service=album_service,
            event_bus=event_bus,
        )

    def create_detail_vm(self) -> DetailViewModel:
        from iPhoto.application.services.asset_service import AssetService

        asset_service = self._container.resolve(AssetService)
        event_bus = self._container.resolve(EventBus)
        return DetailViewModel(
            asset_service=asset_service,
            event_bus=event_bus,
        )


# ---------------------------------------------------------------------------
# Minimal no-op stand-ins when real services are not yet registered.
# ---------------------------------------------------------------------------

class _NoopDataSource:
    def load_assets(self, album_id: str) -> list:
        return []


class _NoopThumbnailCache:
    def get(self, asset_id: str) -> None:
        return None


def _noop_data_source() -> _NoopDataSource:
    return _NoopDataSource()


def _noop_thumbnail_cache() -> _NoopThumbnailCache:
    return _NoopThumbnailCache()
