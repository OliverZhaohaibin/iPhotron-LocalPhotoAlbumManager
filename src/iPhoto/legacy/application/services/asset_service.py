import logging
from typing import List, Optional
from pathlib import Path

from iPhoto.application.ports import AssetFavoriteQueryPort, LibraryStateRepositoryPort
from iPhoto.domain.models import Asset
from iPhoto.domain.models.query import AssetQuery
from iPhoto.legacy.domain.repositories import IAssetRepository

class AssetService:
    """
    Application Service Facade for Asset operations.
    Directly uses Repository for queries (CQRS Query side) or simple operations.
    For complex write operations, it should delegate to Use Cases.

    Optionally wraps a :class:`WeakAssetCache` to avoid redundant DB lookups
    for recently accessed assets.
    """
    def __init__(
        self,
        asset_repo: IAssetRepository,
        import_uc=None,
        move_uc=None,
        metadata_uc=None,
        weak_cache=None,
    ):
        self._repo = asset_repo
        self._import_uc = import_uc
        self._move_uc = move_uc
        self._metadata_uc = metadata_uc
        self._weak_cache = weak_cache
        self._library_root: Path | None = None
        self._session_state_repo: LibraryStateRepositoryPort | None = None
        self._session_favorite_query: AssetFavoriteQueryPort | None = None
        self._logger = logging.getLogger(__name__)

    def set_repository(self, repo: IAssetRepository) -> None:
        self._repo = repo

    def bind_library_surfaces(
        self,
        *,
        library_root: Path,
        state_repository: LibraryStateRepositoryPort,
        favorite_query: AssetFavoriteQueryPort,
    ) -> None:
        """Bind session-owned asset/state surfaces for active-library writes."""

        self._library_root = Path(library_root)
        self._session_state_repo = state_repository
        self._session_favorite_query = favorite_query

    def clear_library_surfaces(self) -> None:
        """Clear active-library surfaces and fall back to legacy repository APIs."""

        self._library_root = None
        self._session_state_repo = None
        self._session_favorite_query = None

    def find_assets(self, query: AssetQuery) -> List[Asset]:
        return self._repo.find_by_query(query)

    def count_assets(self, query: AssetQuery) -> int:
        return self._repo.count(query)

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        # Check weak cache first
        if self._weak_cache is not None:
            cached = self._weak_cache.get(asset_id)
            if cached is not None:
                return cached
        asset = self._repo.get(asset_id)
        if asset is not None and self._weak_cache is not None:
            try:
                self._weak_cache.put(asset_id, asset)
            except TypeError:
                pass  # object does not support weak references
        return asset

    def toggle_favorite(self, asset_id: str) -> bool:
        """Toggles the favorite status of an asset."""
        if self._weak_cache is not None:
            self._weak_cache.invalidate(asset_id)
        asset = self._repo.get(asset_id)
        if asset:
            asset.is_favorite = not asset.is_favorite
            self._repo.save(asset)
            if self._weak_cache is not None:
                self._weak_cache.invalidate(asset_id)
            return asset.is_favorite
        return False

    def toggle_favorite_by_path(self, path: Path) -> bool:
        """Toggles the favorite status of an asset by path."""
        if (
            self._library_root is not None
            and self._session_state_repo is not None
            and self._session_favorite_query is not None
        ):
            rel = self._library_relative_path(path)
            current_state = self._session_favorite_query.favorite_status_for_path(path)
            if current_state is None:
                self._logger.warning("Favorite toggle skipped; asset row not found: %s", rel)
                return False

            new_state = not current_state
            self._session_state_repo.set_favorite_status(rel, new_state)
            if self._weak_cache is not None:
                clear = getattr(self._weak_cache, "clear", None)
                if callable(clear):
                    clear()
            return new_state

        asset = self._repo.get_by_path(path)
        if asset:
            if self._weak_cache is not None:
                self._weak_cache.invalidate(asset.id)
            asset.is_favorite = not asset.is_favorite
            self._repo.save(asset)
            if self._weak_cache is not None:
                self._weak_cache.invalidate(asset.id)
            return asset.is_favorite
        return False

    def _library_relative_path(self, path: Path) -> str:
        if self._library_root is None:
            return Path(path).as_posix()
        candidate = Path(path)
        if not candidate.is_absolute():
            return candidate.as_posix()
        try:
            return candidate.resolve().relative_to(self._library_root.resolve()).as_posix()
        except (OSError, ValueError):
            try:
                return candidate.relative_to(self._library_root).as_posix()
            except ValueError:
                return candidate.name

    def import_assets(self, paths: list[Path], album_id: str, copy: bool = True):
        """Delegate to ImportAssetsUseCase"""
        if self._import_uc:
            from iPhoto.legacy.application.use_cases.import_assets import ImportAssetsRequest
            return self._import_uc.execute(ImportAssetsRequest(
                source_paths=paths,
                target_album_id=album_id,
                copy_files=copy,
            ))
        raise NotImplementedError("ImportAssetsUseCase not configured")

    def move_assets(self, asset_ids: list[str], target_album_id: str):
        """Delegate to MoveAssetsUseCase"""
        if self._move_uc:
            from iPhoto.legacy.application.use_cases.move_assets import MoveAssetsRequest
            return self._move_uc.execute(MoveAssetsRequest(
                asset_ids=asset_ids,
                target_album_id=target_album_id,
            ))
        raise NotImplementedError("MoveAssetsUseCase not configured")

    def update_metadata(self, asset_id: str, metadata: dict):
        """Delegate to UpdateMetadataUseCase"""
        if self._metadata_uc:
            from iPhoto.legacy.application.use_cases.update_metadata import UpdateMetadataRequest
            return self._metadata_uc.execute(UpdateMetadataRequest(
                asset_id=asset_id,
                metadata=metadata,
            ))
        raise NotImplementedError("UpdateMetadataUseCase not configured")
