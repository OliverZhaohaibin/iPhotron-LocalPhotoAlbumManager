"""Compatibility container for the legacy domain-repository use-case graph.

The active desktop runtime is assembled through RuntimeContext/LibrarySession.
Do not add new runtime bindings here unless they are explicitly compatibility
bridges for old tests or isolated callers.
"""

from __future__ import annotations

import logging
from pathlib import Path

from iPhoto.application.interfaces import IMetadataProvider, IThumbnailGenerator
from ..application.services.album_service import AlbumService
from ..application.services.asset_service import AssetService
from ..application.use_cases.open_album import OpenAlbumUseCase
from ..application.use_cases.pair_live_photos import PairLivePhotosUseCase
from ..application.use_cases.scan_album import ScanAlbumUseCase
from iPhoto.di.container import DependencyContainer
from ..domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.events.bus import EventBus
from iPhoto.infrastructure.db.pool import ConnectionPool
from ..infrastructure.repositories.sqlite_album_repository import SQLiteAlbumRepository
from ..infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from iPhoto.infrastructure.services.metadata_provider import ExifToolMetadataProvider
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator


def _default_global_index_db_path() -> Path:
    db_path = Path.home() / ".iPhoto" / "global_index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def build_container(*, db_path: Path | None = None) -> DependencyContainer:
    """Build the application container with the default desktop bindings."""

    container = DependencyContainer()

    pool = ConnectionPool(db_path or _default_global_index_db_path())
    container.register_instance(ConnectionPool, pool)

    logger = logging.getLogger("EventBus")
    container.register_factory(EventBus, lambda: EventBus(logger), singleton=True)

    container.register_singleton(IMetadataProvider, ExifToolMetadataProvider)
    container.register_singleton(IThumbnailGenerator, PillowThumbnailGenerator)

    container.register_factory(
        IAlbumRepository,
        lambda: SQLiteAlbumRepository(container.resolve(ConnectionPool)),
        singleton=True,
    )
    container.register_factory(
        IAssetRepository,
        lambda: SQLiteAssetRepository(container.resolve(ConnectionPool)),
        singleton=True,
    )

    container.register_factory(
        OpenAlbumUseCase,
        lambda: OpenAlbumUseCase(
            album_repo=container.resolve(IAlbumRepository),
            asset_repo=container.resolve(IAssetRepository),
            event_bus=container.resolve(EventBus),
        ),
    )
    container.register_factory(
        ScanAlbumUseCase,
        lambda: ScanAlbumUseCase(
            album_repo=container.resolve(IAlbumRepository),
            asset_repo=container.resolve(IAssetRepository),
            event_bus=container.resolve(EventBus),
            metadata_provider=container.resolve(IMetadataProvider),
            thumbnail_generator=container.resolve(IThumbnailGenerator),
        ),
    )
    container.register_factory(
        PairLivePhotosUseCase,
        lambda: PairLivePhotosUseCase(
            asset_repo=container.resolve(IAssetRepository),
            event_bus=container.resolve(EventBus),
        ),
    )

    container.register_factory(
        AlbumService,
        lambda: AlbumService(
            open_album_use_case=container.resolve(OpenAlbumUseCase),
            scan_album_use_case=container.resolve(ScanAlbumUseCase),
            pair_live_photos_use_case=container.resolve(PairLivePhotosUseCase),
        ),
        singleton=True,
    )
    container.register_factory(
        AssetService,
        lambda: AssetService(asset_repo=container.resolve(IAssetRepository)),
        singleton=True,
    )

    return container


__all__ = ["build_container"]
