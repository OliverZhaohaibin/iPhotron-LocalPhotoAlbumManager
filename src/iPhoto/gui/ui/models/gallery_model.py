"""QML-compatible model for the gallery grid view."""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)

from ....cache.index_store import IndexStore
from ....library.manager import LibraryManager
from ....utils.logging import get_logger

LOGGER = get_logger()


class GalleryRoles(IntEnum):
    """Custom roles for the gallery model exposed to QML."""
    
    RelPathRole = Qt.ItemDataRole.UserRole + 1
    AbsPathRole = Qt.ItemDataRole.UserRole + 2
    ThumbnailRole = Qt.ItemDataRole.UserRole + 3
    IsImageRole = Qt.ItemDataRole.UserRole + 4
    IsVideoRole = Qt.ItemDataRole.UserRole + 5
    IsLiveRole = Qt.ItemDataRole.UserRole + 6
    WidthRole = Qt.ItemDataRole.UserRole + 7
    HeightRole = Qt.ItemDataRole.UserRole + 8
    DateTimeRole = Qt.ItemDataRole.UserRole + 9


class GalleryModel(QAbstractListModel):
    """QML-compatible model for displaying assets in a grid.
    
    This model loads assets from the library index and provides
    thumbnail paths for display.
    """
    
    # Signals
    countChanged = Signal()  # noqa: N815
    
    def __init__(self, library: LibraryManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._items: list[dict] = []
        self._current_album_path: Path | None = None
        self._show_all_photos = False
    
    def roleNames(self) -> dict[int, bytes]:  # noqa: N802  # Qt override
        """Return the role names for QML property binding."""
        return {
            GalleryRoles.RelPathRole: b"relPath",
            GalleryRoles.AbsPathRole: b"absPath",
            GalleryRoles.ThumbnailRole: b"thumbnail",
            GalleryRoles.IsImageRole: b"isImage",
            GalleryRoles.IsVideoRole: b"isVideo",
            GalleryRoles.IsLiveRole: b"isLive",
            GalleryRoles.WidthRole: b"width",
            GalleryRoles.HeightRole: b"height",
            GalleryRoles.DateTimeRole: b"dateTime",
        }
    
    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802  # Qt override
        """Return the number of items."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._items)
    
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Return data for the given role at the specified index."""
        row = index.row()
        if not index.isValid() or row < 0 or row >= len(self._items):
            return None
        
        item = self._items[row]
        library_root = self._library.root()
        
        if role == GalleryRoles.RelPathRole:
            return item.get("rel", "")
        elif role == GalleryRoles.AbsPathRole:
            rel = item.get("rel", "")
            if library_root and rel:
                return str(library_root / rel)
            return ""
        elif role == GalleryRoles.ThumbnailRole:
            # Return full path for Image source
            rel = item.get("rel", "")
            if library_root and rel:
                return "file:///" + str(library_root / rel)
            return ""
        elif role == GalleryRoles.IsImageRole:
            media_type = item.get("media_type", "")
            return media_type in ("image", "heic", "jpeg", "png", "raw")
        elif role == GalleryRoles.IsVideoRole:
            media_type = item.get("media_type", "")
            return media_type in ("video", "mov", "mp4")
        elif role == GalleryRoles.IsLiveRole:
            live_role = item.get("live_role", 0)
            return live_role > 0
        elif role == GalleryRoles.WidthRole:
            return item.get("w", 0) or 0
        elif role == GalleryRoles.HeightRole:
            return item.get("h", 0) or 0
        elif role == GalleryRoles.DateTimeRole:
            ts = item.get("ts")
            if ts:
                return str(ts)
            return ""
        
        return None
    
    @Property(int, notify=countChanged)
    def count(self) -> int:
        """Return the number of items for QML binding."""
        return len(self._items)
    
    @Slot(str)
    def loadAlbum(self, album_path: str) -> None:  # noqa: N802  # Qt slot uses camelCase
        """Load assets from a specific album."""
        self._show_all_photos = False
        path = Path(album_path)
        self._current_album_path = path
        self._load_assets_from_path(path)
    
    @Slot()
    def loadAllPhotos(self) -> None:  # noqa: N802  # Qt slot uses camelCase
        """Load all photos from the library."""
        self._show_all_photos = True
        self._current_album_path = None
        self._load_all_assets()
    
    @Slot()
    def clear(self) -> None:
        """Clear all items from the model."""
        self.beginResetModel()
        self._items = []
        self._current_album_path = None
        self._show_all_photos = False
        self.endResetModel()
        self.countChanged.emit()
    
    def _load_assets_from_path(self, album_path: Path) -> None:
        """Load assets from a specific album path."""
        library_root = self._library.root()
        if library_root is None:
            self.clear()
            return
        
        self.beginResetModel()
        self._items = []
        
        try:
            store = IndexStore(library_root)
            # Get relative album path from library root
            try:
                relative_album = album_path.relative_to(library_root).as_posix()
            except ValueError:
                relative_album = album_path.name
            
            # Read assets for the album
            rows = list(store.read_album_assets(relative_album, include_subalbums=True))
            self._items = rows
        except (OSError, ValueError) as e:
            LOGGER.warning("Error loading album assets: %s", e)
            self._items = []
        
        self.endResetModel()
        self.countChanged.emit()
    
    def _load_all_assets(self) -> None:
        """Load all assets from the library."""
        library_root = self._library.root()
        if library_root is None:
            self.clear()
            return
        
        self.beginResetModel()
        self._items = []
        
        try:
            store = IndexStore(library_root)
            # Read all assets
            rows = list(store.read_all())
            self._items = rows
        except (OSError, ValueError) as e:
            LOGGER.warning("Error loading all assets: %s", e)
            self._items = []
        
        self.endResetModel()
        self.countChanged.emit()


__all__ = ["GalleryModel", "GalleryRoles"]
