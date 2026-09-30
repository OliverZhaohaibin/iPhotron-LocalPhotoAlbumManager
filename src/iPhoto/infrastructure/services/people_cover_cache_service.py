from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from iPhoto.people.image_utils import create_cover_thumbnail, load_image_rgb
from iPhoto.utils.image_loader import load_qimage

_LOGGER = logging.getLogger(__name__)


class PeopleCoverWorkerSignals(QObject):
    result = Signal(str, QImage)
    resultWithGeneration = Signal(int, str, QImage)


class PeopleCoverRenderTask(QRunnable):
    def __init__(
        self,
        *,
        cache_key: str,
        renderer: Callable[[], Optional[QImage]],
        signals: PeopleCoverWorkerSignals,
        disk_file: Path | None = None,
        generation: int = 0,
    ) -> None:
        super().__init__()
        self._cache_key = cache_key
        self._disk_file = disk_file
        self._renderer = renderer
        self._signals = signals
        self._generation = int(generation)

    def run(self) -> None:
        should_write = False
        try:
            image = QImage()
            if self._disk_file is not None and self._disk_file.is_file():
                image = load_qimage(self._disk_file) or QImage()
            if image.isNull():
                image = self._renderer()
                should_write = (
                    image is not None
                    and not image.isNull()
                    and self._disk_file is not None
                )
        except Exception:
            image = None
        if image is None or image.isNull():
            image = QImage()
        self._signals.result.emit(self._cache_key, image)
        self._signals.resultWithGeneration.emit(self._generation, self._cache_key, image)
        # Result delivery is latency-sensitive; persistent caching is not.  In
        # particular, Windows virus scanners can make a PNG write surprisingly
        # slow, so notify the GUI before doing that I/O.
        if should_write and self._disk_file is not None:
            try:
                self._disk_file.parent.mkdir(parents=True, exist_ok=True)
                image.save(str(self._disk_file), "PNG")
            except OSError:
                _LOGGER.debug(
                    "Failed to persist People cover cache file %s",
                    self._disk_file,
                    exc_info=True,
                )


class PeopleCoverCacheService(QObject):
    coverReady = Signal(str)

    def __init__(self, disk_cache_path: Path, memory_limit_items: int = 512) -> None:
        super().__init__()
        self._disk_cache_path = Path(disk_cache_path)
        self._memory_cache: dict[str, QPixmap] = {}
        self._pending_tasks: set[str] = set()
        self._active_signals: dict[tuple[int, str], PeopleCoverWorkerSignals] = {}
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(2)
        self._thread_pool.setThreadPriority(QThread.Priority.NormalPriority)
        self._memory_limit_items = max(32, int(memory_limit_items))
        self._is_shutting_down = False
        self._generation = 0

    def shutdown(self) -> None:
        self._is_shutting_down = True
        self._generation += 1
        self._pending_tasks.clear()
        self._memory_cache.clear()
        self._thread_pool.clear()
        self._thread_pool.waitForDone(1500)
        self._active_signals.clear()

    def set_disk_cache_path(self, disk_cache_path: Path) -> None:
        next_path = Path(disk_cache_path)
        if self._disk_cache_path == next_path:
            return
        self._disk_cache_path = next_path
        self._generation += 1
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
    ) -> tuple[str | None, Optional[QPixmap]]:
        if self._is_shutting_down:
            return None, None
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

        if cache_key not in self._pending_tasks:
            self._pending_tasks.add(cache_key)
            worker_signals = PeopleCoverWorkerSignals()
            worker_signals.resultWithGeneration.connect(
                self._handle_render_result_for_generation
            )
            # QRunnable auto-deletion may release its last Python reference
            # before a queued cross-thread signal is delivered on PySide/Windows.
            # Keep the QObject alive until the GUI-thread callback runs.
            self._active_signals[(self._generation, cache_key)] = worker_signals
            worker = PeopleCoverRenderTask(
                cache_key=cache_key,
                disk_file=self._disk_file(cache_key),
                renderer=renderer,
                signals=worker_signals,
                generation=self._generation,
            )
            self._thread_pool.start(worker)
        return None

    def _handle_render_result_for_generation(
        self,
        generation: int,
        cache_key: str,
        image: QImage,
    ) -> None:
        self._active_signals.pop((int(generation), cache_key), None)
        if int(generation) != self._generation:
            return
        self._handle_render_result(cache_key, image)

    def _handle_render_result(self, cache_key: str, image: QImage) -> None:
        self._pending_tasks.discard(cache_key)
        if self._is_shutting_down:
            return
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        self._remember(cache_key, pixmap)
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
