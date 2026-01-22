import logging
from typing import List, Optional, Any, Dict
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal, Slot, QSize
from PySide6.QtGui import QPixmap

from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.gui.ui.models.roles import Roles, role_names
from src.iPhoto.gui.viewmodels.asset_data_source import AssetDataSource

logger = logging.getLogger(__name__)

class AssetListViewModel(QAbstractListModel):
    """
    New MVVM-based list model for assets.
    Replaces the legacy AssetListModel.
    """
    countChanged = Signal()

    def __init__(self, data_source: AssetDataSource, parent=None):
        super().__init__(parent)
        self._data_source = data_source
        self._assets: List[Asset] = []
        self._current_album_id: Optional[str] = None
        self._current_album_path: Optional[str] = None
        self._filter_mode: Optional[str] = None
        self._library_root: Optional[Path] = None

        # Mapping for O(1) lookups if needed
        self._asset_map: Dict[str, Asset] = {}

    def set_filter_mode(self, mode: Optional[str]):
        """Set the active filter mode (e.g., 'videos', 'favorites')."""
        self._filter_mode = mode
        self.refresh()

    def ensure_chronological_order(self):
        """Ensure assets are sorted chronologically.

        This is a no-op as the repository query handles sorting by default.
        Kept for compatibility with NavigationController.
        """
        pass

    def prioritize_rows(self, first: int, last: int):
        """Prioritize loading for a range of rows. (No-op stub)"""
        pass

    def invalidate_thumbnail(self, rel: str):
        """Invalidate thumbnail for path. (No-op stub)"""
        pass

    def set_library_root(self, root: Path):
        """Update library root path."""
        self._library_root = root

    def album_root(self) -> Optional[Path]:
        """Return the current album path."""
        if self._current_album_path:
            return Path(self._current_album_path)
        return None

    def populate_from_cache(self) -> bool:
        """Synchronously load cached data (No-op stub)."""
        return False

    def start_load(self):
        """Start loading data (triggers refresh)."""
        self.refresh()

    class _DummyThumbnailLoader:
        def shutdown(self): pass
        def invalidate(self, rel): pass

    def thumbnail_loader(self):
        """Return thumbnail loader. (Stub returning dummy object)"""
        return self._DummyThumbnailLoader()

    def update_featured_status(self, rel: str, is_featured: bool):
        """Update featured status for an asset identified by relative path."""
        # Find asset by path
        found_index = -1
        found_asset = None

        for i, asset in enumerate(self._assets):
            if str(asset.path) == rel:
                found_index = i
                found_asset = asset
                break

        if found_asset:
            # Update state (Asset is dataclass, can mutate if not frozen, or replace)
            found_asset.is_favorite = is_featured
            index = self.index(found_index, 0)
            self.dataChanged.emit(index, index, [Roles.FEATURED])

    def load_album(self, album_id: str = None, album_path: str = None):
        """Load assets for the specified album ID or Path."""
        self._current_album_id = album_id
        self._current_album_path = album_path
        self.refresh()

    def refresh(self):
        """Reload data from the source."""
        if not self._current_album_id and not self._current_album_path:
            return

        query = AssetQuery()
        if self._current_album_id:
            query.album_id = self._current_album_id
        if self._current_album_path:
            query.album_path = self._current_album_path

        if self._filter_mode:
            mode = self._filter_mode.casefold()
            if mode == "videos":
                query.media_types = [MediaType.VIDEO]
            elif mode == "favorites":
                query.is_favorite = True
            elif mode == "live":
                query.media_types = [MediaType.LIVE_PHOTO]

        new_assets = self._data_source.fetch_assets(query=query)

        # For simplicity, full reset. Optimization (diffing) can be added later (Phase 5).
        self.beginResetModel()
        self._assets = new_assets
        self._asset_map = {a.id: a for a in self._assets}
        self.endResetModel()
        self.countChanged.emit()

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._assets)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._assets)):
            return None

        asset = self._assets[index.row()]

        if role == Roles.ASSET_ID:
            return asset.id
        elif role == Roles.REL:
            return str(asset.path)
        elif role == Roles.ABS:
            # Construct absolute path using library root
            path_obj = asset.path

            # If path is already absolute, return it
            if path_obj.is_absolute():
                return str(path_obj)

            if self._library_root:
                return str(self._library_root / path_obj)

            if self._current_album_path:
                # Fallback to album path if no library root (though less likely for global assets)
                return str(Path(self._current_album_path) / path_obj)

            return str(path_obj)
        elif role == Roles.IS_IMAGE:
            return asset.media_type == MediaType.IMAGE
        elif role == Roles.IS_VIDEO:
            return asset.media_type == MediaType.VIDEO
        elif role == Roles.IS_LIVE:
            return asset.media_type == MediaType.LIVE_PHOTO
        elif role == Roles.SIZE:
            return asset.size_bytes
        elif role == Roles.DT:
            return asset.created_at
        elif role == Roles.FEATURED:
            return asset.is_favorite
        elif role == Roles.LIVE_GROUP_ID:
            return asset.live_photo_group_id
        elif role == Roles.MICRO_THUMBNAIL:
            # If we have micro thumb in metadata, return it?
            # Domain Asset has metadata dict.
            return asset.metadata.get("micro_thumbnail")
        elif role == Qt.DecorationRole:
            # Return thumbnail.
            # In the legacy model, this triggered async load and returned None or cached pixmap.
            # Here we should probably delegate to a ThumbnailService.
            # For now return None to avoid blocking.
            return None

        return None

    def roleNames(self) -> Dict[int, bytes]:
        return role_names(super().roleNames())

    def get_asset_at(self, row: int) -> Optional[Asset]:
        if 0 <= row < len(self._assets):
            return self._assets[row]
        return None
