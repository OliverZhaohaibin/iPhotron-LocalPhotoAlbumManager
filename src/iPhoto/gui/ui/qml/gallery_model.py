"""QML-compatible model for the gallery grid view."""

from __future__ import annotations

import base64
import logging
import sqlite3
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

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
    from iPhoto.library.manager import LibraryManager
    from iPhoto.errors import IPhotoError
except ImportError:
    from src.iPhoto.library.manager import LibraryManager
    from src.iPhoto.errors import IPhotoError


class GalleryRoles(IntEnum):
    """Custom roles for the gallery model exposed to QML."""
    
    FilePathRole = Qt.ItemDataRole.UserRole + 1
    FileNameRole = Qt.ItemDataRole.UserRole + 2
    ThumbnailUrlRole = Qt.ItemDataRole.UserRole + 3
    MicroThumbRole = Qt.ItemDataRole.UserRole + 4
    IsVideoRole = Qt.ItemDataRole.UserRole + 5
    IsLiveRole = Qt.ItemDataRole.UserRole + 6
    IsPanoRole = Qt.ItemDataRole.UserRole + 7
    IsFavoriteRole = Qt.ItemDataRole.UserRole + 8
    DurationRole = Qt.ItemDataRole.UserRole + 9
    IndexRole = Qt.ItemDataRole.UserRole + 10


logger = logging.getLogger(__name__)


@dataclass
class GalleryItem:
    """Internal representation of a gallery item."""
    
    file_path: Path
    is_video: bool = False
    is_live: bool = False
    is_pano: bool = False
    is_favorite: bool = False
    duration: float = 0.0
    micro_thumbnail: str | None = None


class GalleryModel(QAbstractListModel):
    """QML-compatible list model for the gallery grid.
    
    This model provides asset data to QML views and connects to
    the existing library infrastructure.
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
        self._items: list[GalleryItem] = []
        self._current_path: Path | None = None
        self._loading = False
        
    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802  # Qt override
        """Return the role names for QML property binding."""
        return {
            GalleryRoles.FilePathRole: QByteArray(b"filePath"),
            GalleryRoles.FileNameRole: QByteArray(b"fileName"),
            GalleryRoles.ThumbnailUrlRole: QByteArray(b"thumbnailUrl"),
            GalleryRoles.MicroThumbRole: QByteArray(b"microThumbnail"),
            GalleryRoles.IsVideoRole: QByteArray(b"isVideo"),
            GalleryRoles.IsLiveRole: QByteArray(b"isLive"),
            GalleryRoles.IsPanoRole: QByteArray(b"isPano"),
            GalleryRoles.IsFavoriteRole: QByteArray(b"isFavorite"),
            GalleryRoles.DurationRole: QByteArray(b"duration"),
            GalleryRoles.IndexRole: QByteArray(b"itemIndex"),
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
        elif role == GalleryRoles.MicroThumbRole:
            return item.micro_thumbnail
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

        root = self._library.root()
        album_rel = None
        if root is not None:
            try:
                album_rel = album_path.resolve().relative_to(root)
            except ValueError:
                pass

        self._loading = True
        self.loadingChanged.emit()

        self.beginResetModel()
        self._items.clear()
        self._current_path = album_path

        loaded = False
        if root is not None and album_rel is not None:
            loaded = self._load_from_index(album_rel.as_posix(), include_subalbums=False)
        if not loaded:
            # Fall back to direct filesystem scan if index data is unavailable.
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
        
        loaded = self._load_from_index(None, include_subalbums=True)
        if not loaded:
            # Recursively scan library for media files as a fallback
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
            is_pano = self._check_is_pano(path)
            
            item = GalleryItem(
                file_path=path,
                is_video=False,
                is_live=is_live,
                is_pano=is_pano,
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
                duration=self._get_video_duration(path),
            )
            self._items.append(item)

    def _load_from_index(self, album_rel: str | None, include_subalbums: bool) -> bool:
        """Populate items using the indexed database when available.

        Returns True if at least one row was loaded, False otherwise.
        """
        root = self._library.root()
        if root is None:
            return False

        try:
            from iPhoto.cache.index_store import IndexStore
        except ImportError:
            try:
                from src.iPhoto.cache.index_store import IndexStore
            except ImportError as exc:
                logger.debug("IndexStore unavailable: %s", exc)
                return False

        store = IndexStore(root)
        loaded = False
        try:
            if album_rel:
                rows_iter = store.read_album_assets(
                    album_rel,
                    include_subalbums=include_subalbums,
                    sort_by_date=True,
                    filter_hidden=True,
                )
            else:
                rows_iter = store.read_all(sort_by_date=True, filter_hidden=True)

            for row in rows_iter:
                rel = row.get("rel")
                if not rel:
                    continue
                abs_path = root / rel
                if not abs_path.exists():
                    continue

                media_type = row.get("media_type")
                is_video = bool(media_type == 1 or row.get("is_video"))
                is_live = bool(row.get("live_partner_rel"))
                is_favorite = bool(row.get("is_favorite"))
                duration_val = row.get("dur") or 0.0

                micro_thumb_blob = row.get("micro_thumbnail")
                micro_data = None
                if isinstance(micro_thumb_blob, (bytes, bytearray)) and micro_thumb_blob:
                    try:
                        micro_data = "data:image/jpeg;base64," + base64.b64encode(
                            bytes(micro_thumb_blob)
                        ).decode("ascii")
                    except (ValueError, UnicodeDecodeError) as exc:
                        logger.debug("Failed to decode micro thumbnail for %s: %s", rel, exc)
                        micro_data = None

                item = GalleryItem(
                    file_path=abs_path,
                    is_video=is_video,
                    is_live=is_live,
                    is_pano=False,
                    is_favorite=is_favorite,
                    duration=float(duration_val) if duration_val else 0.0,
                    micro_thumbnail=micro_data,
                )
                self._items.append(item)
                loaded = True
        except (sqlite3.Error, OSError, IPhotoError) as exc:
            # If the index store is unavailable or query fails, fall back to scan logic.
            logger.debug(
                "Index lookup failed for %s: %s",
                album_rel or "<all>",
                exc,
                exc_info=True,
            )
            self._items.clear()
            loaded = False
        return loaded
    
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
    
    def _check_is_pano(self, path: Path) -> bool:
        """Check if the image is a panorama.
        
        Note: This is a placeholder implementation. Full panorama detection
        would require checking EXIF metadata for panorama markers or analyzing
        the image aspect ratio. For now, this returns False to avoid false
        positives. A proper implementation would be added when the detail view
        is migrated to QML.
        """
        # TODO: Implement proper panorama detection using EXIF metadata
        # - Check for GPano namespace in XMP data
        # - Check for panorama-specific EXIF tags
        # - Consider aspect ratio heuristics (e.g., width > 2 * height)
        return False
    
    def _check_is_favorite(self, path: Path) -> bool:
        """Check if the file is marked as a favorite."""
        # Check for favorite marker file
        marker = path.parent / f".{path.name}.favorite"
        return marker.exists()
    
    def _get_video_duration(self, path: Path) -> float:
        """Get the duration of a video file in seconds.
        
        Note: This is a placeholder implementation. Full video duration
        detection would require using ffprobe, PyAV, or similar libraries.
        For now, this returns 0.0 which will display '0:00' for videos.
        A proper implementation would be added when video playback is
        migrated to QML.
        """
        # TODO: Implement proper video duration extraction using:
        # - PyAV (already available in the project)
        # - ffprobe subprocess call
        # - Or integrate with existing media metadata extraction
        return 0.0


__all__ = ["GalleryModel", "GalleryRoles", "GalleryItem"]
