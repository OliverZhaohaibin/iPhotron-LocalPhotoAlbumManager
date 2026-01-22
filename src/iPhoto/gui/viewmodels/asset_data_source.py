from typing import List, Optional, cast
from pathlib import Path
from PySide6.QtCore import QObject, Signal

from src.iPhoto.domain.models import Asset
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.application.dtos import AssetDTO

class AssetDataSource(QObject):
    """
    Intermediary between Repository and ViewModel.
    Handles paging logic and data fetching, and converts Domain Entities to DTOs.
    """

    dataChanged = Signal()

    def __init__(self, repository: IAssetRepository, library_root: Optional[Path] = None):
        super().__init__()
        self._repo = repository
        self._library_root = library_root
        self._current_query: Optional[AssetQuery] = None
        self._cached_dtos: List[AssetDTO] = []
        self._total_count: int = 0
        self._page_size = 1000

    def set_library_root(self, root: Path):
        self._library_root = root

    def load(self, query: AssetQuery):
        """Loads data for the given query."""
        self._current_query = query
        self._cached_dtos.clear()

        # Default limit if not set
        if not query.limit:
            query.limit = 5000

        assets = self._repo.find_by_query(query)
        self._cached_dtos = [self._to_dto(a) for a in assets]
        self._total_count = len(self._cached_dtos)

        self.dataChanged.emit()

    def asset_at(self, index: int) -> Optional[AssetDTO]:
        if 0 <= index < len(self._cached_dtos):
            return self._cached_dtos[index]
        return None

    def count(self) -> int:
        return len(self._cached_dtos)

    def _to_dto(self, asset: Asset) -> AssetDTO:
        # Resolve absolute path
        abs_path = asset.path # Default to path if already absolute
        if not asset.path.is_absolute():
            if self._library_root:
                try:
                    abs_path = (self._library_root / asset.path).resolve()
                except OSError:
                    abs_path = self._library_root / asset.path
            else:
                # Fallback if no library root (should be rare in valid app state)
                abs_path = Path(asset.path).resolve()

        # Determine derived flags
        # Assuming asset.media_type is an Enum or compatible string
        mt = str(asset.media_type)
        is_video = (mt == "video")
        # Live photo check: if asset has live_photo_group_id or explicit type
        is_live = (mt == "live") or (asset.live_photo_group_id is not None)

        # Pano check: usually in metadata
        is_pano = False
        if asset.metadata and asset.metadata.get("is_pano"):
            is_pano = True

        return AssetDTO(
            id=asset.id,
            abs_path=abs_path,
            rel_path=asset.path,
            media_type=mt,
            created_at=asset.created_at,
            width=asset.width or 0,
            height=asset.height or 0,
            duration=asset.duration or 0.0,
            size_bytes=asset.size_bytes,
            metadata=asset.metadata,
            is_favorite=asset.is_favorite,
            is_live=is_live,
            is_pano=is_pano
        )
