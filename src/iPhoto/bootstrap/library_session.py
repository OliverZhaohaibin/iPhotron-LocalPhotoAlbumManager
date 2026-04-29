"""Library-scoped runtime session for vNext application boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..application.ports import AssetRepositoryPort, LibraryStateRepositoryPort
from ..infrastructure.repositories.library_state_repository import (
    IndexStoreLibraryStateRepository,
)
from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime
from ..infrastructure.services.location_metadata_service import (
    ExifToolLocationMetadataService,
)
from ..application.services.assign_location_service import AssignLocationService
from .library_scan_service import LibraryScanService


@dataclass
class LibrarySession:
    """Own library-scoped adapters and expose the application-facing surface."""

    library_root: Path
    asset_runtime: LibraryAssetRuntime = field(default_factory=LibraryAssetRuntime)
    state_repository: LibraryStateRepositoryPort | None = None
    scans: LibraryScanService | None = None
    bind_asset_runtime: bool = True

    def __post_init__(self) -> None:
        self.library_root = Path(self.library_root)
        if self.bind_asset_runtime:
            self.asset_runtime.bind_library_root(self.library_root)
        if self.state_repository is None:
            self.state_repository = IndexStoreLibraryStateRepository(self.library_root)
        if self.scans is None:
            self.scans = LibraryScanService(self.library_root)

    @property
    def assets(self) -> AssetRepositoryPort:
        return self.asset_runtime.repository

    @property
    def thumbnails(self):
        return self.asset_runtime.thumbnail_service

    @property
    def state(self) -> LibraryStateRepositoryPort:
        assert self.state_repository is not None
        return self.state_repository

    def assign_location_service(self) -> AssignLocationService:
        return AssignLocationService(self.state, ExifToolLocationMetadataService())

    def shutdown(self) -> None:
        self.asset_runtime.shutdown()


def create_headless_library_session(root: Path) -> LibrarySession:
    """Create a library session for non-GUI entry points such as the CLI."""

    return LibrarySession(Path(root))


__all__ = ["LibrarySession", "create_headless_library_session"]
