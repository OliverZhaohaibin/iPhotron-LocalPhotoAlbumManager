from __future__ import annotations
from typing import Any, Optional

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

class AssetListViewModel(QAbstractListModel):

    # Custom Roles matching the View's expectations
    # Assuming standard roles + custom ones.
    # We need to check what roles AssetGrid expects.
    # Usually Qt.UserRole + X.

    # Based on existing code inspection (implied):
    ThumbnailRole = Qt.UserRole + 1
    PathRole = Qt.UserRole + 2
    TypeRole = Qt.UserRole + 3 # Image/Video
    DurationRole = Qt.UserRole + 4
    FavoriteRole = Qt.UserRole + 5

    # Compatibility with legacy roles if needed

    def __init__(self, data_source: AssetDataSource, thumbnail_service: ThumbnailCacheService, parent=None):
        super().__init__(parent)
        self._data_source = data_source
        self._thumbnails = thumbnail_service
        self._thumb_size = QSize(256, 256)

        # Connect signals
        self._data_source.dataChanged.connect(self._on_source_changed)

    def load_query(self, query: AssetQuery):
        self._data_source.load(query)

    def rowCount(self, parent=QModelIndex()) -> int:
        return self._data_source.count()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        row = index.row()
        asset = self._data_source.asset_at(row)
        if not asset:
            return None

        # Note: In PySide6, Qt.DisplayRole (etc) are Enums (ItemDataRole).
        # Comparing Enum vs Int might fail if not careful.
        # But signals/slots usually pass int.
        # Let's cast to int to be safe or compare against .value if needed.
        # Actually, PySide6 usually handles this, but let's try direct int comparison just in case

        try:
            role_int = int(role)
        except (ValueError, TypeError):
            # If it's an enum, get its value. If it's something else, try best effort.
            if hasattr(role, 'value'):
                role_int = role.value
            else:
                return None

        # Check standard roles first
        # Qt.DisplayRole is 0.
        if role_int == 0 or role_int == int(Qt.ItemDataRole.DisplayRole):
            return asset.path.name

        # Check custom roles
        if role_int == int(self.ThumbnailRole):
            return self._thumbnails.get_thumbnail(asset.path, self._thumb_size)

        if role_int == int(self.PathRole):
            return str(asset.path)

        if role_int == int(self.TypeRole):
            return asset.media_type.value

        if role_int == int(self.FavoriteRole):
            return asset.is_favorite

        return None

    def _on_source_changed(self):
        self.beginResetModel()
        self.endResetModel()

    # --- View Interface Helpers ---

    @Slot(int, result="QVariant")
    def get(self, row: int):
        # QML helper
        idx = self.index(row, 0)
        return self.data(idx, self.PathRole)
