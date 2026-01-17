"""QML image providers for exposing bundled icons and asset thumbnails."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

from ....config import WORK_DIR_NAME
from ....utils import image_loader

# Import video frame grabber - may not be available in all contexts
try:
    from ..tasks.video_frame_grabber import grab_video_frame
    _HAS_VIDEO_GRABBER = True
except ImportError:
    _HAS_VIDEO_GRABBER = False
    grab_video_frame = None  # type: ignore

# Video extensions that need frame extraction
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".qt"}

if TYPE_CHECKING:  # pragma: no cover
    from PySide6.QtCore import QByteArray

# Path to bundled icon directory
ICON_DIRECTORY = Path(__file__).resolve().parent.parent / "icon"


class IconImageProvider(QQuickImageProvider):
    """QML image provider that loads bundled SVG icons with optional colorization.
    
    Usage in QML:
        Image { source: "image://icons/photo.on.rectangle.svg" }
        Image { source: "image://icons/photo.on.rectangle.svg?color=#007AFF" }
    """
    
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Pixmap)
    
    def requestPixmap(  # noqa: N802 - Qt override
        self, 
        id_str: str, 
        size: QSize, 
        requested_size: QSize
    ) -> QPixmap:
        """Load an SVG icon and optionally colorize it.
        
        The id_str format is: "icon_name.svg?color=#RRGGBB"
        """
        # Parse the ID string
        color: QColor | None = None
        icon_name = id_str
        
        if "?" in id_str:
            icon_name, params = id_str.split("?", 1)
            for param in params.split("&"):
                if param.startswith("color="):
                    color_str = param[6:]
                    color = QColor(color_str)
        
        # Add .svg extension if missing
        if not icon_name.lower().endswith(".svg"):
            icon_name += ".svg"
        
        icon_path = ICON_DIRECTORY / icon_name
        
        if not icon_path.exists():
            # Return a fallback empty pixmap
            fallback_size = requested_size if requested_size.isValid() else QSize(24, 24)
            return QPixmap(fallback_size)
        
        # Load the SVG
        renderer = QSvgRenderer(str(icon_path))
        
        # Get the SVG's native size
        native_size = renderer.defaultSize()
        if not native_size.isValid() or native_size.width() <= 0 or native_size.height() <= 0:
            native_size = QSize(24, 24)
        
        # Determine target size while preserving aspect ratio
        if requested_size.isValid() and requested_size.width() > 0 and requested_size.height() > 0:
            # Calculate size that fits within requested bounds while preserving aspect ratio
            native_aspect = native_size.width() / native_size.height()
            requested_aspect = requested_size.width() / requested_size.height()
            
            if native_aspect > requested_aspect:
                # SVG is wider than requested - fit to width
                target_width = requested_size.width()
                target_height = int(target_width / native_aspect) if native_aspect > 0 else target_width
            else:
                # SVG is taller than requested - fit to height
                target_height = requested_size.height()
                target_width = int(target_height * native_aspect)
            
            target_size = QSize(max(1, target_width), max(1, target_height))
        else:
            target_size = native_size
        
        # Create pixmap and render
        pixmap = QPixmap(target_size)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        
        # Apply color tint if requested
        if color is not None and color.isValid():
            tinted = QPixmap(pixmap.size())
            tinted.fill(Qt.GlobalColor.transparent)
            painter = QPainter(tinted)
            painter.fillRect(tinted.rect(), color)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()
            pixmap = tinted
        
        return pixmap


class ThumbnailImageProvider(QQuickImageProvider):
    """QML image provider for asset thumbnails.
    
    This provider connects to the existing thumbnail loading infrastructure
    and exposes thumbnails to QML components. It uses the same cache path
    generation logic as thumbnail_loader.py for compatibility.
    
    Usage in QML:
        Image { source: "image://thumbnails/" + model.filePath }
    """
    
    # Maximum cache size in bytes (default 100MB)
    MAX_CACHE_SIZE = 100 * 1024 * 1024
    
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache: dict[str, QImage] = {}
        self._cache_order: list[str] = []  # LRU order tracking
        self._cache_size = 0
        self._library_root: Optional[Path] = None

    def set_library_root(self, root: Path) -> None:
        """Set the library root path for locating cached thumbnails."""
        self._library_root = root
        # Clear cache when library root changes
        self.clear_cache()
        
    def requestImage(  # noqa: N802 - Qt override
        self,
        id_str: str,
        size: QSize,
        requested_size: QSize
    ) -> QImage:
        """Load a thumbnail image for the given file path."""
        # Check cache and update LRU order
        if id_str in self._cache:
            # Move to end of LRU list (most recently used)
            if id_str in self._cache_order:
                self._cache_order.remove(id_str)
            self._cache_order.append(id_str)
            
            cached = self._cache[id_str]
            if requested_size.isValid():
                return cached.scaled(
                    requested_size, 
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            return cached
        
        # Try to find a cached thumbnail file first
        image = QImage()
        file_path = Path(id_str)
        
        # Attempt to find cached thumbnail in .iPhoto/thumbs
        # Use the same path resolution as thumbnail_loader.py for cache key compatibility
        if self._library_root:
            try:
                # Resolve the absolute path - this matches thumbnail_loader.py behavior
                # The cache key in thumbnail_loader.py is based on str(abs_path.resolve())
                try:
                    abs_path = file_path.resolve()
                    path_str = str(abs_path)
                except OSError:
                    path_str = id_str
                    
                digest = hashlib.blake2b(path_str.encode("utf-8"), digest_size=20).hexdigest()
                thumbs_dir = self._library_root / WORK_DIR_NAME / "thumbs"

                if thumbs_dir.exists():
                    # Find any file starting with digest_
                    # The filenames are digest_stamp_WxH.png
                    candidates = []
                    prefix = f"{digest}_"
                    for entry in thumbs_dir.iterdir():
                        if entry.name.startswith(prefix) and entry.suffix == ".png":
                            candidates.append(entry)

                    if candidates:
                        # Sort by mtime descending to get latest
                        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                        best_candidate = candidates[0]
                        # Load from cache file
                        image.load(str(best_candidate))
            except Exception:
                # Fallback to loading original if cache lookup fails
                pass

        # Fallback: Load original file if no cache found or cache load failed
        # Use image_loader which supports HEIC/HEIF via Pillow fallback
        if image.isNull() and file_path.exists():
            target_size = requested_size if requested_size.isValid() else QSize(512, 512)
            
            # Check if this is a video file
            suffix = file_path.suffix.lower()
            if suffix in VIDEO_EXTENSIONS and _HAS_VIDEO_GRABBER:
                # Try to extract a frame from the video
                try:
                    loaded_image = grab_video_frame(file_path, target_size)
                except Exception:
                    loaded_image = None
            else:
                # Load image file using image_loader (supports HEIC/HEIF)
                loaded_image = image_loader.load_qimage(file_path, target_size)
            
            if loaded_image is not None and not loaded_image.isNull():
                image = loaded_image
        
        if image.isNull():
            # Return placeholder
            placeholder_size = requested_size if requested_size.isValid() else QSize(192, 192)
            placeholder = QImage(placeholder_size, QImage.Format.Format_ARGB32)
            placeholder.fill(QColor("#1b1b1b"))
            return placeholder
        
        # Cache the loaded image with LRU eviction
        image_size = image.sizeInBytes()
        
        # Evict old entries if cache is too large
        while self._cache_size + image_size > self.MAX_CACHE_SIZE and self._cache_order:
            oldest_key = self._cache_order.pop(0)
            if oldest_key in self._cache:
                old_image = self._cache.pop(oldest_key)
                self._cache_size -= old_image.sizeInBytes()
        
        self._cache[id_str] = image
        self._cache_order.append(id_str)
        self._cache_size += image_size
        
        # Scale if requested
        if requested_size.isValid():
            image = image.scaled(
                requested_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
        
        return image
    
    def clear_cache(self) -> None:
        """Clear the thumbnail cache."""
        self._cache.clear()
        self._cache_order.clear()
        self._cache_size = 0


__all__ = ["IconImageProvider", "ThumbnailImageProvider", "ICON_DIRECTORY"]
