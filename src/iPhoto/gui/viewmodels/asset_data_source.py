from typing import List, Optional
from src.iPhoto.domain.models import Asset
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.domain.repositories import IAssetRepository

class AssetDataSource:
    """Data source for asset views, abstracting repository access."""

    def __init__(self, asset_repository: IAssetRepository):
        self._repo = asset_repository

    def fetch_assets(self, album_id: Optional[str] = None, query: Optional[AssetQuery] = None) -> List[Asset]:
        """Fetch assets for a given album or query."""
        if query is None:
            query = AssetQuery()

        if album_id:
            query.album_id = album_id

        return self._repo.find_by_query(query)

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        return self._repo.get(asset_id)
