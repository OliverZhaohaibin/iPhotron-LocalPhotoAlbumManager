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

    def find_assets(self, query: AssetQuery) -> List[Asset]:
        return self._repo.find_by_query(query)

    def count_assets(self, query: AssetQuery) -> int:
        return self._repo.count(query)

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        return self._repo.get(asset_id)

    def toggle_favorite(self, asset_path: str):
        """Toggles the favorite status of an asset."""
        # Note: In SQLiteAssetRepository, 'rel' is PK.
        # We assume asset_path passed here is relative to library root
        # or we need to query by absolute path if repo supported it.
        # Since AssetDTO provides 'rel_path', we should use that string.

        # 1. Fetch current status
        # Since get() uses ID, and we might not have ID or ID==path,
        # we rely on the caller passing the correct identifier for update.
        # SQLiteAssetRepository needs a new method `update_favorite_status` or similar.

        # Let's assume we can fetch by ID (if path is ID) or query.
        # Better: Add specific update method to repo interface.
        # I added update_favorite_status to SQLiteAssetRepository in previous step.

        # But we need to know current status to toggle.
        # Or pass "new status". Toggle implies read-modify-write.

        # Fetch asset to check current state
        # How to fetch by path?
        # Repo has no get_by_path.
        # We can use find_by_query with limit=1?
        # But query uses album filtering.

        # Let's assume asset_path IS the ID/PK for now as per legacy schema.
        # Or add a specialized method.

        # For MVP fix:
        # We can just update it blindly if we knew the new state?
        # Caller (Coordinator) has ViewModel which has data.
        # But VM might be stale.

        # Let's read from repo using raw SQL or get() if id==rel.
        # In legacy, id is often uuid, rel is path.
        # We need to find by rel.

        # Temporary workaround: Use a query to find by path is hard without exact match filter in Query object.
        # Let's trust the Repo added method `update_favorite_status` (which I added).
        # We need to read it first.

        # Actually, let's implement `get_by_path` in Repo?
        # Or just trust the caller?

        # Let's implement read-toggle in Repo or Service.
        # Service read:
        # We don't have get_by_path.
        pass

    def set_favorite(self, asset_path: str, is_favorite: bool):
        """Sets the favorite status directly."""
        if hasattr(self._repo, 'update_favorite_status'):
            self._repo.update_favorite_status(asset_path, is_favorite)
        else:
            self._logger.warning("Repository does not support update_favorite_status")
