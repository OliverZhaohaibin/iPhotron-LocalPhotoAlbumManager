from __future__ import annotations
from typing import Any, Optional, Dict, cast

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    QSize,
    Slot
)
from PySide6.QtGui import QPixmap

from src.iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from src.iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.gui.ui.models.roles import Roles
from src.iPhoto.application.dtos import AssetDTO
from pathlib import Path

class AssetListViewModel(QAbstractListModel):
    """
    ViewModel backing the asset grid and filmstrip.
    Adapts AssetDTOs to Qt Roles expected by AssetGridDelegate.
    """

    def __init__(self, data_source: AssetDataSource, thumbnail_service: ThumbnailCacheService, parent=None):
        super().__init__(parent)
        self._data_source = data_source
        self._thumbnails = thumbnail_service
        self._thumb_size = QSize(256, 256)

        # Connect signals
        self._data_source.dataChanged.connect(self._on_source_changed)

    def load_query(self, query: AssetQuery):
        """Triggers data loading for a new query."""
        self._data_source.load(query)

    def rowCount(self, parent=QModelIndex()) -> int:
        return self._data_source.count()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        row = index.row()
        asset: Optional[AssetDTO] = self._data_source.asset_at(row)
        if not asset:
            return None

        # Convert Enum/Flag roles to int if necessary (PySide6 usually passes int)
        role_int = int(role)

        # --- Standard Roles ---
        if role_int == Qt.ItemDataRole.DisplayRole:
            return asset.rel_path.name

        if role_int == Qt.DecorationRole:
            # Main thumbnail
            return self._thumbnails.get_thumbnail(asset.abs_path, self._thumb_size)

        if role_int == Qt.ItemDataRole.ToolTipRole:
            return str(asset.abs_path)

        # --- Custom Roles (AssetModel contract) ---

        if role_int == Roles.REL:
            return str(asset.rel_path)

        if role_int == Roles.ABS:
            return str(asset.abs_path)

        if role_int == Roles.ASSET_ID:
            return asset.id

        if role_int == Roles.IS_IMAGE:
            return asset.is_image

        if role_int == Roles.IS_VIDEO:
            return asset.is_video

        if role_int == Roles.IS_LIVE:
            return asset.is_live

        if role_int == Roles.SIZE:
            # Delegate expects a dict with 'duration', 'width', 'height' etc.
            # or just for duration extraction.
            return {
                "duration": asset.duration,
                "width": asset.width,
                "height": asset.height,
                "bytes": asset.size_bytes
            }

        if role_int == Roles.DT:
            return asset.created_at

        if role_int == Roles.FEATURED:
            return asset.is_favorite

        if role_int == Roles.MICRO_THUMBNAIL:
            # Optional: Return a tiny cached image if available
            return asset.micro_thumbnail

        if role_int == Roles.INFO:
            # Metadata dictionary
            info = asset.metadata.copy() if asset.metadata else {}
            # Ensure critical keys exist for InfoPanel
            info.update({
                "rel": str(asset.rel_path),
                "abs": str(asset.abs_path),
                "w": asset.width,
                "h": asset.height,
                "dur": asset.duration,
                "bytes": asset.size_bytes
            })
            return info

        if role_int == Roles.IS_PANO:
            return asset.is_pano

        if role_int == Roles.IS_SPACER:
            return False # ViewModels usually don't have spacers, unless inserted

        if role_int == Roles.IS_CURRENT:
            # State management should ideally be separate or injected.
            # For now, we return False as selection state is usually handled by SelectionModel
            # But AssetDelegate might use this for visual highlight in Filmstrip
            # This requires the VM to know about "current" which makes it stateful.
            # We can handle this by an external controller updating the model, or separate state.
            return False

        return None

    def _on_source_changed(self):
        self.beginResetModel()
        self.endResetModel()

    # --- QML / Scriptable Helpers ---

    @Slot(int, result="QVariant")
    def get(self, row: int):
        idx = self.index(row, 0)
        return self.data(idx, Roles.ABS)

    def invalidate_thumbnail(self, path_str: str):
        """Forces a thumbnail refresh for the given path."""
        # Find the row(s) matching this path
        # In a real app, use a path->row index for speed.
        # Here we do a linear scan for simplicity (assuming path_str is absolute or relative).

        path = Path(path_str)
        # Invalidate in service first
        self._thumbnails.invalidate(path)

        # Notify views
        count = self.rowCount()
        for row in range(count):
            asset = self._data_source.asset_at(row)
            if asset:
                # Check match (abs or rel)
                if str(asset.abs_path) == path_str or str(asset.rel_path) == path_str:
                    idx = self.index(row, 0)
                    self.dataChanged.emit(idx, idx, [Qt.DecorationRole])
                    break

    def thumbnail_loader(self):
        # Legacy compatibility hook
        # The new architecture shouldn't expose the loader directly,
        # but AssetModel proxy might call this.
        # We return a dummy or wrapper if needed, or update AssetModel to use service.
        pass

    def prioritize_rows(self, first: int, last: int):
        # Hint to data source / thumbnail service to prefetch
        # self._data_source.prefetch(first, last)
        pass
