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
        
        # Determine size
        target_size = requested_size if requested_size.isValid() else renderer.defaultSize()
        if not target_size.isValid():
            target_size = QSize(24, 24)
        
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
    and exposes thumbnails to QML components.
    
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
        
        # Attempt to find cached thumbnail in .lexiphoto/thumbs
        if self._library_root and file_path.exists():
            try:
                # Logic matching generate_cache_path in thumbnail_loader.py
                path_str = str(file_path.resolve())
                digest = hashlib.blake2b(path_str.encode("utf-8"), digest_size=20).hexdigest()
                thumbs_dir = self._library_root / WORK_DIR_NAME / "thumbs"

                if thumbs_dir.exists():
                    # Find any file starting with digest_
                    # The filenames are digest_stamp_WxH.png
                    # We want the most recent one if possible, or just any valid one.
                    # Since we don't know the exact size or stamp, we search.

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
        if image.isNull() and file_path.exists():
            image.load(str(file_path))
        
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
