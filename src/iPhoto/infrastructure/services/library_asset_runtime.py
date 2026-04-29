"""Runtime-owned asset repository and thumbnail service rebinding."""

from __future__ import annotations

from pathlib import Path

from ...config import WORK_DIR_NAME
from ...domain.repositories import IAssetRepository
from ...utils.pathutils import ensure_work_dir
from ..db.pool import ConnectionPool
from ..repositories.sqlite_asset_repository import SQLiteAssetRepository
from .thumbnail_cache_service import ThumbnailCacheService


class LibraryAssetRuntime:
    """Own library-bound asset services so GUI code only rebinds roots."""

    def __init__(self, library_root: Path | None = None) -> None:
        self._pool: ConnectionPool | None = None
        self._repository: IAssetRepository
        self._thumbnail_service = ThumbnailCacheService(self._cache_root(library_root))
        self.bind_library_root(library_root)

    @property
    def repository(self) -> IAssetRepository:
        return self._repository

    @property
    def thumbnail_service(self) -> ThumbnailCacheService:
        return self._thumbnail_service

    def bind_library_root(self, library_root: Path | None) -> None:
        """Rebuild the asset repository and cache path for *library_root*."""

        db_path = self._database_path(library_root)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        next_pool = ConnectionPool(db_path)
        next_repository = SQLiteAssetRepository(next_pool)

        previous_pool = self._pool
        self._pool = next_pool
        self._repository = next_repository
        self._thumbnail_service.set_disk_cache_path(self._cache_root(library_root))

        if previous_pool is not None:
            previous_pool.close_all()

    def shutdown(self) -> None:
        self._thumbnail_service.shutdown()
        if self._pool is not None:
            self._pool.close_all()
            self._pool = None

    def _database_path(self, library_root: Path | None) -> Path:
        if library_root is None:
            return Path.home() / ".iPhoto" / "global_index.db"
        return ensure_work_dir(library_root) / "global_index.db"

    def _cache_root(self, library_root: Path | None) -> Path:
        if library_root is None:
            return Path.home() / WORK_DIR_NAME / "cache" / "thumbs"
        return ensure_work_dir(library_root) / "cache" / "thumbs"
