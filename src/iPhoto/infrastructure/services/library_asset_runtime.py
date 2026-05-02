"""Runtime-owned asset repository and thumbnail service rebinding."""

from __future__ import annotations

from pathlib import Path

from ...application.ports import AssetRepositoryPort, EditServicePort
from ...config import WORK_DIR_NAME
from ...cache.index_store import get_global_repository
from ...domain.repositories import IAssetRepository
from ...utils.pathutils import ensure_work_dir
from ..repositories.index_store_asset_repository import IndexStoreAssetRepositoryAdapter
from .thumbnail_cache_service import ThumbnailCacheService


class LibraryAssetRuntime:
    """Own library-bound asset services so GUI code only rebinds roots."""

    def __init__(self, library_root: Path | None = None) -> None:
        self._assets: AssetRepositoryPort
        self._repository: IAssetRepository
        self._thumbnail_service = ThumbnailCacheService(self._cache_root(library_root))
        self.bind_library_root(library_root)

    @property
    def assets(self) -> AssetRepositoryPort:
        return self._assets

    @property
    def repository(self) -> IAssetRepository:
        return self._repository

    @property
    def thumbnail_service(self) -> ThumbnailCacheService:
        return self._thumbnail_service

    def bind_edit_service(self, edit_service: EditServicePort | None) -> None:
        """Bind the current library session edit surface into thumbnail rendering."""

        setter = getattr(self._thumbnail_service, "set_edit_service", None)
        if callable(setter):
            setter(edit_service)

    def bind_library_root(self, library_root: Path | None) -> None:
        """Rebuild the asset repository and cache path for *library_root*."""

        next_assets = get_global_repository(self._repository_root(library_root))
        self._assets = next_assets
        self._repository = IndexStoreAssetRepositoryAdapter(next_assets)
        self._thumbnail_service.set_disk_cache_path(self._cache_root(library_root))

    def shutdown(self) -> None:
        self._thumbnail_service.shutdown()
        close = getattr(self._assets, "close", None)
        if callable(close):
            close()

    def _repository_root(self, library_root: Path | None) -> Path:
        if library_root is None:
            return Path.home()
        return Path(library_root)

    def _cache_root(self, library_root: Path | None) -> Path:
        if library_root is None:
            return Path.home() / WORK_DIR_NAME / "cache" / "thumbs"
        return ensure_work_dir(library_root) / "cache" / "thumbs"
