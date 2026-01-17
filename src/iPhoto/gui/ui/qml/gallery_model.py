"""QML-compatible model for the gallery grid view."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
    Property,
    QUrl,
    QByteArray,
)
from PySide6.QtGui import QImage

try:
    from .....library.manager import LibraryManager
except ImportError:
    try:
        from src.iPhoto.library.manager import LibraryManager
    except ImportError:
        from iPhoto.library.manager import LibraryManager

try:
    from .....cache.index_store import IndexStore
except ImportError:
    try:
        from src.iPhoto.cache.index_store import IndexStore
    except ImportError:
        from iPhoto.cache.index_store import IndexStore


class GalleryRoles(IntEnum):
    """Custom roles for the gallery model exposed to QML."""
    
    FilePathRole = Qt.ItemDataRole.UserRole + 1
    FileNameRole = Qt.ItemDataRole.UserRole + 2
    ThumbnailUrlRole = Qt.ItemDataRole.UserRole + 3
    IsVideoRole = Qt.ItemDataRole.UserRole + 4
    IsLiveRole = Qt.ItemDataRole.UserRole + 5
    IsPanoRole = Qt.ItemDataRole.UserRole + 6
    IsFavoriteRole = Qt.ItemDataRole.UserRole + 7
    DurationRole = Qt.ItemDataRole.UserRole + 8
    IndexRole = Qt.ItemDataRole.UserRole + 9
    MicroThumbnailUrlRole = Qt.ItemDataRole.UserRole + 10


@dataclass
class GalleryItem:
    """Internal representation of a gallery item."""
    
    file_path: Path
    rel_path: str = ""
    is_video: bool = False
    is_live: bool = False
    is_pano: bool = False
    is_favorite: bool = False
    duration: float = 0.0
    micro_thumbnail: Optional[bytes] = field(default=None, repr=False)


class GalleryModel(QAbstractListModel):
    """QML-compatible list model for the gallery grid.
    
    This model provides asset data to QML views and connects to
    the existing library infrastructure. It uses the database index
    for fast loading and supports micro thumbnails for instant placeholders.
    """
    
    # Signal for notifying when an item is selected
    itemSelected = Signal(str)  # noqa: N815
    itemDoubleClicked = Signal(str)  # noqa: N815
    countChanged = Signal()  # noqa: N815
    loadingChanged = Signal()  # noqa: N815
    
    # Supported image extensions
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif"}
    VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
    LIVE_SUFFIX = "_live"
    
    def __init__(self, library: LibraryManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._items: List[GalleryItem] = []
        self._current_path: Optional[Path] = None
        self._loading = False
        
    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802  # Qt override
        """Return the role names for QML property binding."""
        return {
            GalleryRoles.FilePathRole: QByteArray(b"filePath"),
            GalleryRoles.FileNameRole: QByteArray(b"fileName"),
            GalleryRoles.ThumbnailUrlRole: QByteArray(b"thumbnailUrl"),
            GalleryRoles.IsVideoRole: QByteArray(b"isVideo"),
            GalleryRoles.IsLiveRole: QByteArray(b"isLive"),
            GalleryRoles.IsPanoRole: QByteArray(b"isPano"),
            GalleryRoles.IsFavoriteRole: QByteArray(b"isFavorite"),
            GalleryRoles.DurationRole: QByteArray(b"duration"),
            GalleryRoles.IndexRole: QByteArray(b"itemIndex"),
            GalleryRoles.MicroThumbnailUrlRole: QByteArray(b"microThumbnailUrl"),
        }
    
    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        """Return the number of items in the gallery."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._items)
    
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Return data for the given role at the specified index."""
        row = index.row()
        if not index.isValid() or row < 0 or row >= len(self._items):
            return None
        
        item = self._items[row]
        
        if role == Qt.ItemDataRole.DisplayRole or role == GalleryRoles.FileNameRole:
            return item.file_path.name
        elif role == GalleryRoles.FilePathRole:
            return str(item.file_path)
        elif role == GalleryRoles.ThumbnailUrlRole:
            # Return the URL for the thumbnail image provider
            # Encode as file:// URL to preserve special characters
            file_url = QUrl.fromLocalFile(str(item.file_path)).toString()
            return f"image://thumbnails/{file_url}"
        elif role == GalleryRoles.MicroThumbnailUrlRole:
            # Return the micro thumbnail as a data URL if available
            if item.micro_thumbnail:
                # Micro thumbnails are stored as PNG bytes in the database
                b64_data = base64.b64encode(item.micro_thumbnail).decode('ascii')
                return f"data:image/png;base64,{b64_data}"
            return ""
        elif role == GalleryRoles.IsVideoRole:
            return item.is_video
        elif role == GalleryRoles.IsLiveRole:
            return item.is_live
        elif role == GalleryRoles.IsPanoRole:
            return item.is_pano
        elif role == GalleryRoles.IsFavoriteRole:
            return item.is_favorite
        elif role == GalleryRoles.DurationRole:
            return item.duration
        elif role == GalleryRoles.IndexRole:
            return row
        
        return None
    
    @Property(int, notify=countChanged)
    def count(self) -> int:
        """Return the number of items in the gallery."""
        return len(self._items)
    
    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        """Return whether the gallery is currently loading."""
        return self._loading
    
    @Slot(str)
    def loadAlbum(self, path: str) -> None:  # noqa: N802
        """Load assets from the given album path."""
        album_path = Path(path)
        if not album_path.exists() or not album_path.is_dir():
            return
        
        self._loading = True
        self.loadingChanged.emit()
        
        self.beginResetModel()
        self._items.clear()
        self._current_path = album_path
        
        # Try to load from database index first for speed
        library_root = self._library.root()
        loaded_from_db = False
        
        if library_root:
            try:
                loaded_from_db = self._load_from_database(album_path, library_root)
            except Exception:
                # Fall back to filesystem scan
                loaded_from_db = False
        
        if not loaded_from_db:
            # Scan directory for media files
            self._scan_directory(album_path)
        
        self.endResetModel()
        self.countChanged.emit()
        
        self._loading = False
        self.loadingChanged.emit()
    
    @Slot()
    def loadAllPhotos(self) -> None:  # noqa: N802
        """Load all photos from the library root."""
        root = self._library.root()
        if root is None:
            return
        
        self._loading = True
        self.loadingChanged.emit()
        
        self.beginResetModel()
        self._items.clear()
        self._current_path = root
        
        # Try to load from database index first
        loaded_from_db = False
        try:
            loaded_from_db = self._load_from_database(root, root, include_subalbums=True)
        except Exception:
            loaded_from_db = False
        
        if not loaded_from_db:
            # Recursively scan library for media files
            self._scan_directory_recursive(root)
            # Sort by modification time (newest first)
            self._items.sort(key=lambda x: x.file_path.stat().st_mtime, reverse=True)
        
        self.endResetModel()
        self.countChanged.emit()
        
        self._loading = False
        self.loadingChanged.emit()
    
    @Slot()
    def clear(self) -> None:
        """Clear all items from the gallery."""
        self.beginResetModel()
        self._items.clear()
        self._current_path = None
        self.endResetModel()
        self.countChanged.emit()
    
    @Slot(int)
    def selectItem(self, index: int) -> None:  # noqa: N802
        """Handle item selection at the given index."""
        if 0 <= index < len(self._items):
            item = self._items[index]
            self.itemSelected.emit(str(item.file_path))
    
    @Slot(int)
    def doubleClickItem(self, index: int) -> None:  # noqa: N802
        """Handle double-click on item at the given index."""
        if 0 <= index < len(self._items):
            item = self._items[index]
            self.itemDoubleClicked.emit(str(item.file_path))
    
    def _load_from_database(
        self,
        album_path: Path,
        library_root: Path,
        include_subalbums: bool = False,
    ) -> bool:
        """Load assets from the database index.
        
        Returns True if assets were loaded from the database, False otherwise.
        """
        try:
            index_store = IndexStore(library_root)
        except Exception:
            return False
        
        # Calculate relative album path from library root
        try:
            rel_album_path = album_path.relative_to(library_root).as_posix()
        except ValueError:
            # Album is not under library root
            rel_album_path = ""
        
        # Use read_geometry_only for efficient loading with micro thumbnails
        try:
            rows = list(index_store.read_geometry_only(
                album_path=rel_album_path if rel_album_path else None,
                include_subalbums=include_subalbums,
                sort_by_date=True,
            ))
        except Exception:
            return False
        
        if not rows:
            return False
        
        for row in rows:
            item = self._row_to_gallery_item(row, library_root)
            if item is not None:
                self._items.append(item)
        
        return len(self._items) > 0
    
    def _row_to_gallery_item(
        self,
        row: Dict[str, Any],
        library_root: Path,
    ) -> Optional[GalleryItem]:
        """Convert a database row to a GalleryItem."""
        rel = row.get("rel")
        if not rel:
            return None
        
        file_path = library_root / rel
        
        # Determine media type
        media_type = row.get("media_type", "")
        is_video = media_type == "video"
        
        # Check for live photo partner
        live_partner_rel = row.get("live_partner_rel")
        is_live = bool(live_partner_rel)
        
        # Get favorite status
        is_favorite = bool(row.get("is_favorite", 0))
        
        # Get duration for videos
        duration = float(row.get("dur", 0) or 0)
        
        # Get micro thumbnail bytes
        micro_thumbnail = row.get("micro_thumbnail")
        if isinstance(micro_thumbnail, (bytes, bytearray)):
            micro_thumb_bytes = bytes(micro_thumbnail)
        else:
            micro_thumb_bytes = None
        
        return GalleryItem(
            file_path=file_path,
            rel_path=rel,
            is_video=is_video,
            is_live=is_live,
            is_pano=False,  # Panorama detection would require additional metadata
            is_favorite=is_favorite,
            duration=duration,
            micro_thumbnail=micro_thumb_bytes,
        )
    
    def _scan_directory(self, path: Path) -> None:
        """Scan a single directory for media files."""
        try:
            for entry in sorted(path.iterdir()):
                if entry.is_file():
                    self._add_if_media(entry)
        except PermissionError:
            pass
    
    def _scan_directory_recursive(self, path: Path) -> None:
        """Recursively scan directories for media files."""
        try:
            for entry in path.iterdir():
                if entry.is_file():
                    self._add_if_media(entry)
                elif entry.is_dir() and not entry.name.startswith("."):
                    self._scan_directory_recursive(entry)
        except PermissionError:
            pass
    
    def _add_if_media(self, path: Path) -> None:
        """Add the file to items if it's a media file."""
        suffix = path.suffix.lower()
        
        if suffix in self.IMAGE_EXTENSIONS:
            # Check if it's a live photo
            is_live = self._check_is_live(path)
            
            item = GalleryItem(
                file_path=path,
                is_video=False,
                is_live=is_live,
                is_pano=False,
                is_favorite=self._check_is_favorite(path),
            )
            self._items.append(item)
            
        elif suffix in self.VIDEO_EXTENSIONS:
            item = GalleryItem(
                file_path=path,
                is_video=True,
                is_live=False,
                is_pano=False,
                is_favorite=self._check_is_favorite(path),
                duration=0.0,
            )
            self._items.append(item)
    
    def _check_is_live(self, path: Path) -> bool:
        """Check if the image is part of a Live Photo."""
        # Look for companion video file
        stem = path.stem
        parent = path.parent
        
        for video_ext in self.VIDEO_EXTENSIONS:
            video_path = parent / f"{stem}{video_ext}"
            if video_path.exists():
                return True
            # Also check for _live suffix
            live_video_path = parent / f"{stem}{self.LIVE_SUFFIX}{video_ext}"
            if live_video_path.exists():
                return True
        
        return False
    
    def _check_is_favorite(self, path: Path) -> bool:
        """Check if the file is marked as a favorite."""
        # Check for favorite marker file
        marker = path.parent / f".{path.name}.favorite"
        return marker.exists()


__all__ = ["GalleryModel", "GalleryRoles", "GalleryItem"]
