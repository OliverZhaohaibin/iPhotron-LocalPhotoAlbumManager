import logging
from typing import List, Optional
from pathlib import Path

from iPhoto.domain.models import Asset
from iPhoto.domain.models.query import AssetQuery
from iPhoto.domain.repositories import IAssetRepository

class AssetService:
    """
    Application Service Facade for Asset operations.
    Directly uses Repository for queries (CQRS Query side) or simple operations.
    For complex write operations, it should delegate to Use Cases.
    """
    def __init__(
        self,
        asset_repo: IAssetRepository,
        import_uc=None,
        move_uc=None,
        metadata_uc=None,
    ):
        self._repo = asset_repo
        self._import_uc = import_uc
        self._move_uc = move_uc
        self._metadata_uc = metadata_uc
        self._logger = logging.getLogger(__name__)

    def set_repository(self, repo: IAssetRepository) -> None:
        self._repo = repo

    def find_assets(self, query: AssetQuery) -> List[Asset]:
        return self._repo.find_by_query(query)

    def count_assets(self, query: AssetQuery) -> int:
        return self._repo.count(query)

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        return self._repo.get(asset_id)

    def toggle_favorite(self, asset_id: str) -> bool:
        """Toggles the favorite status of an asset."""
        asset = self._repo.get(asset_id)
        if asset:
            asset.is_favorite = not asset.is_favorite
            self._repo.save(asset)
            return asset.is_favorite
        return False

    def toggle_favorite_by_path(self, path: Path) -> bool:
        """Toggles the favorite status of an asset by path."""
        self._logger.info("[FAV-TOGGLE] Looking up asset by path: %s", path)
        asset = self._repo.get_by_path(path)
        if asset:
            old_state = asset.is_favorite
            asset.is_favorite = not asset.is_favorite
            self._logger.info(
                "[FAV-TOGGLE] Found asset id=%s rel=%s | is_favorite: %s -> %s",
                asset.id, asset.path.as_posix(), old_state, asset.is_favorite,
            )
            self._repo.save(asset)
            # Verify the save by re-reading
            verify = self._repo.get_by_path(path)
            if verify:
                self._logger.info(
                    "[FAV-TOGGLE] Verify after save: id=%s is_favorite=%s",
                    verify.id, verify.is_favorite,
                )
            else:
                self._logger.warning("[FAV-TOGGLE] Verify failed: asset not found after save!")
            return asset.is_favorite
        self._logger.warning("[FAV-TOGGLE] Asset not found for path: %s", path)
        return False

    def import_assets(self, paths: list[Path], album_id: str, copy: bool = True):
        """Delegate to ImportAssetsUseCase"""
        if self._import_uc:
            from iPhoto.application.use_cases.import_assets import ImportAssetsRequest
            return self._import_uc.execute(ImportAssetsRequest(
                source_paths=paths,
                target_album_id=album_id,
                copy_files=copy,
            ))
        raise NotImplementedError("ImportAssetsUseCase not configured")

    def move_assets(self, asset_ids: list[str], target_album_id: str):
        """Delegate to MoveAssetsUseCase"""
        if self._move_uc:
            from iPhoto.application.use_cases.move_assets import MoveAssetsRequest
            return self._move_uc.execute(MoveAssetsRequest(
                asset_ids=asset_ids,
                target_album_id=target_album_id,
            ))
        raise NotImplementedError("MoveAssetsUseCase not configured")

    def update_metadata(self, asset_id: str, metadata: dict):
        """Delegate to UpdateMetadataUseCase"""
        if self._metadata_uc:
            from iPhoto.application.use_cases.update_metadata import UpdateMetadataRequest
            return self._metadata_uc.execute(UpdateMetadataRequest(
                asset_id=asset_id,
                metadata=metadata,
            ))
        raise NotImplementedError("UpdateMetadataUseCase not configured")
