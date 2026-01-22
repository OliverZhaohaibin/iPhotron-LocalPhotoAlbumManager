from typing import List, Optional
from PySide6.QtCore import QObject, Signal

from src.iPhoto.domain.models import Asset
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.domain.repositories import IAssetRepository

class AssetDataSource(QObject):
    """
    Intermediary between Repository and ViewModel.
    Handles paging logic and data fetching.
    """

    dataChanged = Signal()

    def __init__(self, repository: IAssetRepository):
        super().__init__()
        self._repo = repository
        self._current_query: Optional[AssetQuery] = None
        self._cached_assets: List[Asset] = []
        self._total_count: int = 0
        self._page_size = 1000 # Large page for desktop

    def load(self, query: AssetQuery):
        """Loads data for the given query."""
        self._current_query = query
        # Reset
        self._cached_assets.clear()

        # Initial Load (Synchronous for MVP, move to Worker later)
        # We fetch the first batch or all if reasonable.
        # For huge libraries, we'd use offset/limit.

        # Let's try to fetch all for now if it's an album (usual case < 5000)
        # If no limit specified, use reasonable max
        if not query.limit:
            query.limit = 5000

        self._cached_assets = self._repo.find_by_query(query)
        self._total_count = len(self._cached_assets) # In exact paging, use repo.count()

        self.dataChanged.emit()

    def asset_at(self, index: int) -> Optional[Asset]:
        if 0 <= index < len(self._cached_assets):
            return self._cached_assets[index]
        return None

    def count(self) -> int:
        return len(self._cached_assets)

    def index_of(self, asset: Asset) -> int:
        # Simple linear search, optimize if needed
        try:
            return self._cached_assets.index(asset)
        except ValueError:
            return -1
