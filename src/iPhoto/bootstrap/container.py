"""DI container assembly for the application.

This module is the single authoritative location for dependency wiring.
New infrastructure registrations must be added here rather than in
``appctx.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..di.container import DependencyContainer
from ..domain.repositories import IAlbumRepository, IAssetRepository
from ..infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from ..infrastructure.repositories.sqlite_album_repository import SQLiteAlbumRepository
from ..infrastructure.db.pool import ConnectionPool
from ..events.bus import EventBus
from ..application.use_cases.open_album import OpenAlbumUseCase
from ..application.use_cases.scan_album import ScanAlbumUseCase
from ..application.use_cases.pair_live_photos import PairLivePhotosUseCase
from ..application.services.album_service import AlbumService
from ..application.services.asset_service import AssetService
from ..infrastructure.services.metadata_provider import ExifToolMetadataProvider
from ..infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from ..application.interfaces import IMetadataProvider, IThumbnailGenerator


def build_container() -> DependencyContainer:
    """Assemble and return the application DI container."""

    container = DependencyContainer()

    # Infrastructure
    db_path = Path.home() / ".iPhoto" / "global_index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pool = ConnectionPool(db_path)
    container.register_instance(ConnectionPool, pool)

    # Event Bus
    logger = logging.getLogger("EventBus")
    container.register_factory(EventBus, lambda: EventBus(logger), singleton=True)

    # Infrastructure Services
    container.register_singleton(IMetadataProvider, ExifToolMetadataProvider)
    container.register_singleton(IThumbnailGenerator, PillowThumbnailGenerator)

    # Repositories
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

    # Use Cases
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

    # Services
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
