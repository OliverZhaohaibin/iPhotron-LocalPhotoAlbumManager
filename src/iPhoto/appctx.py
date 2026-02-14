"""Application-wide context helpers for the GUI layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, TYPE_CHECKING
import os
import logging
import uuid
from typing import Optional

from .di.container import DependencyContainer
from .domain.repositories import IAlbumRepository, IAssetRepository
from .infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from .infrastructure.repositories.sqlite_album_repository import SQLiteAlbumRepository
from .infrastructure.db.pool import ConnectionPool
from .events.bus import EventBus
from .application.use_cases.open_album import OpenAlbumUseCase
from .application.use_cases.scan_album import ScanAlbumUseCase
from .application.use_cases.pair_live_photos import PairLivePhotosUseCase
from .application.services.album_service import AlbumService
from .application.services.asset_service import AssetService
from .infrastructure.services.metadata_provider import ExifToolMetadataProvider
from .infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from .application.interfaces import IMetadataProvider, IThumbnailGenerator

from .config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, WORK_DIR_NAME

if TYPE_CHECKING:  # pragma: no cover - only for type checking
    from .gui.facade import AppFacade
    from .library.manager import LibraryManager
    from .settings.manager import SettingsManager


def _create_facade() -> "AppFacade":
    """Factory that imports :class:`AppFacade` lazily to avoid circular imports."""

    from .gui.facade import AppFacade  # Local import prevents circular dependency

    return AppFacade()


def _create_settings_manager():
    from .settings.manager import SettingsManager

    manager = SettingsManager()
    manager.load()
    return manager


def _create_library_manager():
    from .library.manager import LibraryManager

    return LibraryManager()

def _create_di_container() -> DependencyContainer:
    container = DependencyContainer()

    # Infrastructure
    db_path = Path.home() / ".iPhoto" / "global_index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Register Connection Pool (Singleton)
    pool = ConnectionPool(db_path)
    container.register_instance(ConnectionPool, pool)

    # Event Bus
    logger = logging.getLogger("EventBus")
    container.register_factory(EventBus, lambda: EventBus(logger), singleton=True)

    # Infrastructure Services
    container.register_singleton(IMetadataProvider, ExifToolMetadataProvider)
    container.register_singleton(IThumbnailGenerator, PillowThumbnailGenerator)

    # Repositories
    container.register_factory(IAlbumRepository,
                               lambda: SQLiteAlbumRepository(container.resolve(ConnectionPool)),
                               singleton=True)

    container.register_factory(IAssetRepository,
                               lambda: SQLiteAssetRepository(container.resolve(ConnectionPool)),
                               singleton=True)

    # Use Cases
    container.register_factory(OpenAlbumUseCase,
                               lambda: OpenAlbumUseCase(
                                   album_repo=container.resolve(IAlbumRepository),
                                   asset_repo=container.resolve(IAssetRepository),
                                   event_bus=container.resolve(EventBus)
                               ))
    container.register_factory(ScanAlbumUseCase,
                               lambda: ScanAlbumUseCase(
                                   album_repo=container.resolve(IAlbumRepository),
                                   asset_repo=container.resolve(IAssetRepository),
                                   event_bus=container.resolve(EventBus),
                                   metadata_provider=container.resolve(IMetadataProvider),
                                   thumbnail_generator=container.resolve(IThumbnailGenerator)
                               ))
    container.register_factory(PairLivePhotosUseCase,
                               lambda: PairLivePhotosUseCase(
                                   asset_repo=container.resolve(IAssetRepository),
                                   event_bus=container.resolve(EventBus)
                               ))

    # Services
    container.register_factory(AlbumService,
                               lambda: AlbumService(
                                   open_album_use_case=container.resolve(OpenAlbumUseCase),
                                   scan_album_use_case=container.resolve(ScanAlbumUseCase),
                                   pair_live_photos_use_case=container.resolve(PairLivePhotosUseCase)
                               ), singleton=True)

    container.register_factory(AssetService,
                               lambda: AssetService(
                                   asset_repo=container.resolve(IAssetRepository)
                               ), singleton=True)

    return container

@dataclass
class AppContext:
    """Container object shared across GUI components."""

    settings: "SettingsManager" = field(default_factory=_create_settings_manager)
    library: "LibraryManager" = field(default_factory=_create_library_manager)
    facade: "AppFacade" = field(default_factory=_create_facade)
    recent_albums: List[Path] = field(default_factory=list)
    theme: "ThemeManager" = field(init=False)

    # DI Container integration
    container: DependencyContainer = field(default_factory=_create_di_container)

    def __post_init__(self) -> None:
        from .errors import LibraryError
        from .gui.ui.theme_manager import ThemeManager

        self.theme = ThemeManager(self.settings)
        self.theme.apply_theme()

        # ``AppFacade`` needs to observe the shared library manager so that
        # manifest writes performed while browsing nested albums can keep the
        # global "Favorites" collection in sync.  The binding is established
        # eagerly here because both collaborators are constructed via default
        # factories before ``__post_init__`` runs.
        self.facade.bind_library(self.library)

        basic_path = self.settings.get("basic_library_path")
        if isinstance(basic_path, str) and basic_path:
            candidate = Path(basic_path).expanduser()
            if candidate.exists():
                try:
                    self.library.bind_path(candidate)
                    self._start_initial_scan_if_needed(candidate)
                except LibraryError as exc:
                    self.library.errorRaised.emit(str(exc))
            else:
                self.library.errorRaised.emit(
                    f"Basic Library path is unavailable: {candidate}"
                )
        stored = self.settings.get("last_open_albums", []) or []
        resolved: list[Path] = []
        for entry in stored:
            try:
                resolved.append(Path(entry))
            except TypeError:
                continue
        if resolved:
            self.recent_albums = resolved[:10]

    def _start_initial_scan_if_needed(self, library_root: Path) -> None:
        work_dir = library_root / WORK_DIR_NAME
        db_path = work_dir / "global_index.db"
        if work_dir.exists() and db_path.exists():
            return
        if self.library.is_scanning_path(library_root):
            return
        self.library.start_scanning(library_root, DEFAULT_INCLUDE, DEFAULT_EXCLUDE)

    def remember_album(self, root: Path) -> None:
        """Track *root* in the recent albums list, keeping the most recent first."""

        normalized = root.resolve()
        self.recent_albums = [entry for entry in self.recent_albums if entry != normalized]
        self.recent_albums.insert(0, normalized)
        # Keep the list short to avoid unbounded growth.
        del self.recent_albums[10:]
        self.settings.set(
            "last_open_albums",
            [str(path) for path in self.recent_albums],
        )
