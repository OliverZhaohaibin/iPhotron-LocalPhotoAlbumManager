import shutil
from pathlib import Path
from typing import Optional, Dict, Set

from PySide6.QtCore import QObject, QSize, Signal, QThreadPool, QRunnable
from PySide6.QtGui import QPixmap, QImage, QImageReader

from src.iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator

# Signals for the worker need to be defined on a QObject subclass
class ThumbnailWorkerSignals(QObject):
    result = Signal(Path, QImage)

class ThumbnailGenerationTask(QRunnable):
    """Background task to generate a thumbnail."""

    def __init__(self, generator, path: Path, size: QSize, signals: ThumbnailWorkerSignals):
        super().__init__()
        self._generator = generator
        self._path = path
        self._size = size
        self._signals = signals

    def run(self):
        try:
            # Generate logic (CPU intensive)
            image = self._generator.generate(self._path, (self._size.width(), self._size.height()))
            if image:
                # Convert PIL Image to QImage safely
                import io
                bio = io.BytesIO()
                image.save(bio, format="JPEG")
                qimg = QImage.fromData(bio.getvalue())

                # Emit result back to main thread
                self._signals.result.emit(self._path, qimg)
        except Exception:
            # Silently fail or log in generator
            pass

class ThumbnailCacheService(QObject):
    """
    Manages thumbnail caching (Memory + Disk) and asynchronous generation.
    """

    thumbnailReady = Signal(Path)

    def __init__(self, disk_cache_path: Path, memory_limit_mb: int = 500):
        super().__init__()
        self._disk_cache_path = disk_cache_path
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._generator = PillowThumbnailGenerator()

        # Simple in-memory cache: Dict[Path, QPixmap]
        # In a real app, use an LRU cache with size tracking.
        self._memory_cache: Dict[str, QPixmap] = {}
        self._max_memory_items = 1000 # Rough approximation

        self._pending_tasks: Set[str] = set()
        self._thread_pool = QThreadPool.globalInstance()

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

        # 3. Trigger Async Generation if not pending
        if key not in self._pending_tasks:
            self._pending_tasks.add(key)
            self._start_generation(path, size)

        # Return placeholder or None while loading
        return None

    def _start_generation(self, path: Path, size: QSize):
        # Create signals object (must be created on heap/managed by QObject tree or kept alive)
        # Since QRunnable isn't a QObject parent, we need to ensure signals exist during run.
        # However, typically we pass a new QObject.
        # But wait, connecting a signal to a slot keeps it alive if the slot receiver is alive?
        # No, the emitter (signals object) must survive until emit() is called.
        # A common pattern is to let the worker hold the reference, but QRunnable auto-deletes.

        # We instantiate signals here. The worker holds a reference to it.
        worker_signals = ThumbnailWorkerSignals()
        worker_signals.result.connect(self._handle_generation_result)

        # We need to ensure worker_signals isn't garbage collected before run() finishes?
        # QThreadPool takes ownership of QRunnable. The QRunnable holds 'signals'.
        # Python ref counting should keep 'signals' alive as long as 'worker' is alive.

        worker = ThumbnailGenerationTask(self._generator, path, size, worker_signals)
        self._thread_pool.start(worker)

    def _handle_generation_result(self, path: Path, image: QImage):
        # Back on main thread
        if not image.isNull():
            # We assume standard thumb request for now (256x256) to match get_thumbnail logic
            # Ideally, we pass the original requested size back via signals to reconstruct exact key.
            target_size = QSize(256, 256)
            key = self._cache_key(path, target_size)

            pixmap = QPixmap.fromImage(image)

            # Save to disk
            disk_file = self._disk_cache_path / f"{key}.jpg"
            pixmap.save(str(disk_file), "JPEG")

            self._add_to_memory(key, pixmap)
            self._pending_tasks.discard(key)

            self.thumbnailReady.emit(path)

    def invalidate(self, path: Path):
        """Removes the thumbnail from cache to force regeneration."""
        size = QSize(256, 256)
        key = self._cache_key(path, size)

        if key in self._memory_cache:
            del self._memory_cache[key]

        disk_file = self._disk_cache_path / f"{key}.jpg"
        if disk_file.exists():
            try:
                disk_file.unlink()
            except OSError:
                pass

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
