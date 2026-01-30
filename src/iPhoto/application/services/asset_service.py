import logging
from typing import List, Optional
from pathlib import Path

from src.iPhoto.domain.models import Asset
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.domain.repositories import IAssetRepository

class AssetService:
    """
    Application Service Facade for Asset operations.
    Directly uses Repository for queries (CQRS Query side) or simple operations.
    For complex write operations, it should delegate to Use Cases.
    """
    def __init__(self, asset_repo: IAssetRepository):
        self._repo = asset_repo
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
        asset = self._repo.get_by_path(path)
        if asset:
            asset.is_favorite = not asset.is_favorite
            self._repo.save(asset)
            return asset.is_favorite
        return False
