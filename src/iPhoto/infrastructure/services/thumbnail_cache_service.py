import shutil
from pathlib import Path
from typing import Optional, Dict

from PySide6.QtCore import QObject, QSize
from PySide6.QtGui import QPixmap, QImage, QImageReader

from src.iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator

class ThumbnailCacheService(QObject):
    """
    Manages thumbnail caching (Memory + Disk) and generation.
    It does NOT depend on the old AssetCacheManager.
    """

    def __init__(self, disk_cache_path: Path, memory_limit_mb: int = 500):
        super().__init__()
        self._disk_cache_path = disk_cache_path
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._generator = PillowThumbnailGenerator()

        # Simple in-memory cache: Dict[Path, QPixmap]
        # In a real app, use an LRU cache with size tracking.
        self._memory_cache: Dict[str, QPixmap] = {}
        self._max_memory_items = 1000 # Rough approximation

    def get_thumbnail(self, path: Path, size: QSize) -> Optional[QPixmap]:
        key = self._cache_key(path, size)

        # 1. Memory Check
        if key in self._memory_cache:
            return self._memory_cache[key]

        # 2. Disk Check
        disk_file = self._disk_cache_path / f"{key}.jpg"
        if disk_file.exists():
            pixmap = QPixmap(str(disk_file))
            if not pixmap.isNull():
                self._add_to_memory(key, pixmap)
                return pixmap

        # 3. Generate (Blocking for now, but usually called in worker)
        # For MVVM, this might return None initially and trigger async load.
        # But to keep it simple for the initial migration:
        try:
            image = self._generator.generate(path, (size.width(), size.height()))
            if image:
                # Convert PIL to QPixmap
                # This is a bit inefficient (PIL -> Data -> QImage -> QPixmap)
                # Ideally generator returns bytes or QImage directly.
                import io
                bio = io.BytesIO()
                image.save(bio, format="JPEG")
                qimg = QImage.fromData(bio.getvalue())
                pixmap = QPixmap.fromImage(qimg)

                # Save to disk
                pixmap.save(str(disk_file), "JPEG")

                self._add_to_memory(key, pixmap)
                return pixmap
        except Exception as e:
            print(f"Error generating thumbnail for {path}: {e}")

        return None

    def _cache_key(self, path: Path, size: QSize) -> str:
        # Simple hash of path + size
        import hashlib
        s = f"{path.as_posix()}_{size.width()}x{size.height()}"
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    def _add_to_memory(self, key: str, pixmap: QPixmap):
        if len(self._memory_cache) > self._max_memory_items:
            # Simple eviction: remove random item (first)
            self._memory_cache.pop(next(iter(self._memory_cache)))
        self._memory_cache[key] = pixmap
