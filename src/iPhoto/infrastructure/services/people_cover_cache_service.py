from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from iPhoto.people.image_utils import create_cover_thumbnail, load_image_rgb


class PeopleCoverWorkerSignals(QObject):
    result = Signal(str, QImage)


class PeopleCoverRenderTask(QRunnable):
    def __init__(
        self,
        *,
        cache_key: str,
        renderer: Callable[[], Optional[QImage]],
        signals: PeopleCoverWorkerSignals,
    ) -> None:
        super().__init__()
        self._cache_key = cache_key
        self._renderer = renderer
        self._signals = signals

    def run(self) -> None:
        try:
            image = self._renderer()
        except Exception:
            image = None
        if image is not None and not image.isNull():
            self._signals.result.emit(self._cache_key, image)


class PeopleCoverCacheService(QObject):
    coverReady = Signal(str)

    def __init__(self, disk_cache_path: Path, memory_limit_items: int = 512) -> None:
        super().__init__()
        self._disk_cache_path = Path(disk_cache_path)
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._memory_cache: dict[str, QPixmap] = {}
        self._pending_tasks: set[str] = set()
        self._thread_pool = QThreadPool.globalInstance()
        self._memory_limit_items = max(32, int(memory_limit_items))
        self._is_shutting_down = False

    def shutdown(self) -> None:
        self._is_shutting_down = True
        self._pending_tasks.clear()
        self._memory_cache.clear()

    def set_disk_cache_path(self, disk_cache_path: Path) -> None:
        next_path = Path(disk_cache_path)
        if self._disk_cache_path == next_path:
            return
        self._disk_cache_path = next_path
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._memory_cache.clear()
        self._pending_tasks.clear()

    def get_thumbnail(self, path: Path, size: tuple[int, int]) -> tuple[str | None, Optional[QPixmap]]:
        if self._is_shutting_down:
            return None, None
        signature = self._path_signature(path)
        if signature is None:
            return None, None
        key = self._cache_key("path", str(path.resolve()), signature, self._size_key(size))
        pixmap = self._get_or_start(
            key,
            lambda: self._render_path_thumbnail(path, size),
        )
        return key, pixmap

    def get_rendered_cover(
        self,
        *,
        cache_id: str,
        size: tuple[int, int],
        signature: str,
        renderer: Callable[[], Optional[QImage]],
    ) -> tuple[str, Optional[QPixmap]]:
        key = self._cache_key("rendered", cache_id, signature, self._size_key(size))
        pixmap = self._get_or_start(key, renderer)
        return key, pixmap

    def cached_pixmap(self, cache_key: str | None) -> Optional[QPixmap]:
        if not cache_key:
            return None
        return self._memory_cache.get(cache_key)

    def _get_or_start(
        self,
        cache_key: str,
        renderer: Callable[[], Optional[QImage]],
    ) -> Optional[QPixmap]:
        pixmap = self._memory_cache.get(cache_key)
        if pixmap is not None:
            return pixmap

        disk_file = self._disk_file(cache_key)
        if disk_file.exists():
            disk_pixmap = QPixmap(str(disk_file))
            if not disk_pixmap.isNull():
                self._remember(cache_key, disk_pixmap)
                return disk_pixmap

        if cache_key not in self._pending_tasks:
            self._pending_tasks.add(cache_key)
            worker_signals = PeopleCoverWorkerSignals()
            worker_signals.result.connect(self._handle_render_result)
            worker = PeopleCoverRenderTask(
                cache_key=cache_key,
                renderer=renderer,
                signals=worker_signals,
            )
            self._thread_pool.start(worker)
        return None

    def _handle_render_result(self, cache_key: str, image: QImage) -> None:
        self._pending_tasks.discard(cache_key)
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        self._remember(cache_key, pixmap)
        pixmap.save(str(self._disk_file(cache_key)), "PNG")
        self.coverReady.emit(cache_key)

    def _remember(self, cache_key: str, pixmap: QPixmap) -> None:
        if len(self._memory_cache) >= self._memory_limit_items:
            self._memory_cache.pop(next(iter(self._memory_cache)))
        self._memory_cache[cache_key] = pixmap

    def _render_path_thumbnail(self, path: Path, size: tuple[int, int]) -> Optional[QImage]:
        if not path.exists():
            return None
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        image = load_image_rgb(path)
        cover = create_cover_thumbnail(image, (width, height))
        data = cover.tobytes("raw", "RGBA")
        return QImage(
            data,
            width,
            height,
            width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()

    def _disk_file(self, cache_key: str) -> Path:
        return self._disk_cache_path / f"{cache_key}.png"

    @staticmethod
    def _size_key(size: tuple[int, int]) -> str:
        return f"{int(size[0])}x{int(size[1])}"

    @staticmethod
    def _cache_key(*parts: str) -> str:
        payload = "\x00".join(parts)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _path_signature(path: Path) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return f"{stat.st_mtime_ns}:{stat.st_size}"
