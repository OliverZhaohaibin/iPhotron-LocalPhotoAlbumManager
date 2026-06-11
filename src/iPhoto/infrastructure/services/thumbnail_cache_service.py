import shutil
import time
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Deque, Dict, Optional, Set

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform

from iPhoto.application.ports import EditServicePort
from iPhoto.core import geo_utils
from iPhoto.core.color_resolver import compute_color_statistics
from iPhoto.core.image_filters import apply_adjustments
from iPhoto.infrastructure.services.performance_events import emit_perf_event, monotonic_ms
from iPhoto.infrastructure.services.thumbnail_cache_keys import (
    thumbnail_cache_file_for_key,
    thumbnail_cache_key,
)
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.io import sidecar
from iPhoto.utils import image_loader


class ThumbnailWorkerSignals(QObject):
    """Signals emitted by thumbnail generation workers."""

    result = Signal(Path, QSize, QImage)
    failed = Signal(Path, QSize, str)


class ThumbnailGenerationTask(QRunnable):
    """Background task to generate a thumbnail."""

    def __init__(
        self,
        renderer,
        path: Path,
        size: QSize,
        signals: ThumbnailWorkerSignals,
    ):
        super().__init__()
        self._renderer = renderer
        self._path = path
        self._size = size
        self._signals = signals

    def run(self):
        try:
            # Generate logic (CPU intensive)
            qimg = self._renderer(self._path, self._size)
            if qimg is not None and not qimg.isNull():
                # Emit result back to main thread
                self._signals.result.emit(self._path, self._size, qimg)
            else:
                self._signals.failed.emit(self._path, self._size, "empty_render")
        except Exception:
            self._signals.failed.emit(self._path, self._size, "exception")

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
        self._edit_service: EditServicePort | None = None

        # Simple in-memory cache: Dict[Path, QPixmap]
        # In a real app, use an LRU cache with size tracking.
        self._memory_cache: Dict[str, QPixmap] = {}
        self._max_memory_items = 1000  # Rough approximation

        self._pending_tasks: Set[str] = set()
        self._queued_tasks: Dict[str, tuple[Path, QSize, str]] = {}
        self._priority_queues: dict[str, Deque[str]] = {
            "visible": deque(),
            "normal": deque(),
            "low": deque(),
        }
        self._active_tasks = 0
        self._max_active_jobs = 2
        self._failure_cooldown_seconds = 60.0
        self._failure_until: Dict[str, float] = {}
        self._thread_pool = QThreadPool.globalInstance()
        self._is_shutting_down = False

    def shutdown(self):
        """Prevents new tasks from being submitted and clears pending logic."""
        self._is_shutting_down = True
        self._pending_tasks.clear()
        self._queued_tasks.clear()
        for queue in self._priority_queues.values():
            queue.clear()
        self._active_tasks = 0

    def set_disk_cache_path(self, disk_cache_path: Path) -> None:
        self._is_shutting_down = False
        if self._disk_cache_path == disk_cache_path:
            return
        self._disk_cache_path = disk_cache_path
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._memory_cache.clear()
        self._pending_tasks.clear()
        self._queued_tasks.clear()
        for queue in self._priority_queues.values():
            queue.clear()
        self._failure_until.clear()

    def set_edit_service(self, edit_service: EditServicePort | None) -> None:
        """Bind the current edit surface used for thumbnail rendering."""

        self._edit_service = edit_service

    def get_thumbnail(self, path: Path, size: QSize, *, priority: str = "normal") -> Optional[QPixmap]:
        if self._is_shutting_down:
            return None

        key = self._cache_key(path, size)
        now = time.monotonic()
        if self._failure_until.get(key, 0.0) > now:
            emit_perf_event("thumbnail_generation_cooldown", key=key)
            return None

        # 1. Memory Check
        if key in self._memory_cache:
            emit_perf_event("thumbnail_cache_hit", tier="L1", key=key)
            return self._memory_cache[key]

        # 2. Disk Check
        disk_file = thumbnail_cache_file_for_key(self._disk_cache_path, key)
        if disk_file.exists():
            pixmap = QPixmap(str(disk_file))
            if not pixmap.isNull():
                self._add_to_memory(key, pixmap)
                emit_perf_event("thumbnail_cache_hit", tier="L2", key=key)
                return pixmap

        # 3. Trigger Async Generation if not pending
        emit_perf_event("thumbnail_cache_miss", key=key, pending=len(self._pending_tasks))
        if key not in self._pending_tasks:
            self._queue_generation(path, size, priority=priority)

        # Return placeholder or None while loading
        return None

    def peek(self, path: Path, size: QSize) -> Optional[QPixmap]:
        """Return an in-memory thumbnail without I/O or background scheduling."""

        if self._is_shutting_down:
            return None
        return self._memory_cache.get(self._cache_key(path, size))

    def request_many(
        self,
        paths: Iterable[Path],
        size: QSize,
        *,
        priority: str = "normal",
    ) -> int:
        """Queue memory misses for asynchronous L2 loading or generation."""

        if self._is_shutting_down:
            return 0

        queued = 0
        now = time.monotonic()
        for path in dict.fromkeys(Path(path) for path in paths):
            key = self._cache_key(path, size)
            if key in self._memory_cache or key in self._pending_tasks:
                continue
            if self._failure_until.get(key, 0.0) > now:
                continue
            self._queue_generation(path, size, priority=priority)
            queued += 1
        return queued

    def cancel_pending_except(self, paths: Set[Path], size: QSize) -> None:
        """Cancel queued thumbnail work except for *paths* at *size*."""

        keep_keys = {self._cache_key(path, size) for path in paths}
        drop_keys = set(self._queued_tasks) - keep_keys
        for key in drop_keys:
            self._queued_tasks.pop(key, None)
            self._pending_tasks.discard(key)
        if drop_keys:
            for priority, queue in self._priority_queues.items():
                self._priority_queues[priority] = deque(
                    key for key in queue if key not in drop_keys
                )

    def _queue_generation(self, path: Path, size: QSize, *, priority: str) -> None:
        priority = priority if priority in self._priority_queues else "normal"
        key = self._cache_key(path, size)
        self._pending_tasks.add(key)
        self._queued_tasks[key] = (path, size, priority)
        self._priority_queues[priority].append(key)
        self._drain_generation_queue()

    def _drain_generation_queue(self) -> None:
        while not self._is_shutting_down and self._active_tasks < self._max_active_jobs:
            next_item = self._pop_next_generation()
            if next_item is None:
                return
            key, path, size = next_item
            self._active_tasks += 1
            self._start_generation(key, path, size)

    def _pop_next_generation(self) -> tuple[str, Path, QSize] | None:
        for priority in ("visible", "normal", "low"):
            queue = self._priority_queues[priority]
            while queue:
                key = queue.popleft()
                spec = self._queued_tasks.pop(key, None)
                if spec is None:
                    continue
                path, size, _priority = spec
                return key, path, size
        return None

    def _start_generation(self, key: str, path: Path, size: QSize):
        # Create signals object (must be created on heap/managed by QObject tree or kept alive)
        # Since QRunnable isn't a QObject parent, we need to ensure signals exist during run.
        # However, typically we pass a new QObject.
        # But wait, connecting a signal to a slot keeps it alive if the slot receiver is alive?
        # No, the emitter (signals object) must survive until emit() is called.
        # A common pattern is to let the worker hold the reference, but QRunnable auto-deletes.

        # We instantiate signals here. The worker holds a reference to it.
        worker_signals = ThumbnailWorkerSignals()
        worker_signals.result.connect(self._handle_generation_result)
        worker_signals.failed.connect(self._handle_generation_failure)

        # We need to ensure worker_signals isn't garbage collected before run() finishes?
        # QThreadPool takes ownership of QRunnable. The QRunnable holds 'signals'.
        # Python ref counting should keep 'signals' alive as long as 'worker' is alive.

        emit_perf_event(
            "thumbnail_generate_started",
            path=path,
            width=size.width(),
            height=size.height(),
            pending=len(self._pending_tasks),
        )
        worker = ThumbnailGenerationTask(self._load_or_render_thumbnail, path, size, worker_signals)
        self._thread_pool.start(worker)

    def _handle_generation_result(self, path: Path, size: QSize, image: QImage):
        # Back on main thread
        if not image.isNull():
            key = self._cache_key(path, size)
            pixmap = QPixmap.fromImage(image)

            self._add_to_memory(key, pixmap)
            self._pending_tasks.discard(key)
            self._failure_until.pop(key, None)
            self._active_tasks = max(0, self._active_tasks - 1)

            emit_perf_event(
                "thumbnail_generate_finished",
                path=path,
                width=size.width(),
                height=size.height(),
                pending=len(self._pending_tasks),
            )
            self.thumbnailReady.emit(path)
            self._drain_generation_queue()

    def _handle_generation_failure(self, path: Path, size: QSize, reason: str) -> None:
        key = self._cache_key(path, size)
        self._pending_tasks.discard(key)
        self._queued_tasks.pop(key, None)
        self._failure_until[key] = time.monotonic() + self._failure_cooldown_seconds
        self._active_tasks = max(0, self._active_tasks - 1)
        emit_perf_event(
            "thumbnail_generate_failed",
            path=path,
            width=size.width(),
            height=size.height(),
            reason=reason,
            pending=len(self._pending_tasks),
        )
        self._drain_generation_queue()

    def invalidate(self, path: Path, *, size: QSize | None = None):
        """Removes the thumbnail from cache to force regeneration."""
        if size is None:
            size = QSize(512, 512)
        key = self._cache_key(path, size)

        if key in self._memory_cache:
            del self._memory_cache[key]
        self._failure_until.pop(key, None)
        self._pending_tasks.discard(key)
        self._queued_tasks.pop(key, None)

        disk_file = thumbnail_cache_file_for_key(self._disk_cache_path, key)
        if disk_file.exists():
            try:
                disk_file.unlink()
            except OSError:
                pass

    def remap_album_paths(self, old_root: Path, new_root: Path, *, size: QSize | None = None) -> None:
        """Copy cached thumbnails from an album's old path to its renamed path."""

        if size is None:
            size = QSize(512, 512)
        if not new_root.exists():
            return
        try:
            paths = [path for path in new_root.rglob("*") if path.is_file()]
        except OSError:
            return

        for new_path in paths:
            try:
                rel = new_path.relative_to(new_root)
            except ValueError:
                continue
            old_path = old_root / rel
            old_key = self._cache_key(old_path, size)
            new_key = self._cache_key(new_path, size)
            if old_key in self._memory_cache and new_key not in self._memory_cache:
                self._memory_cache[new_key] = self._memory_cache[old_key]

            old_disk_file = thumbnail_cache_file_for_key(self._disk_cache_path, old_key)
            new_disk_file = thumbnail_cache_file_for_key(self._disk_cache_path, new_key)
            if old_disk_file.exists() and not new_disk_file.exists():
                try:
                    shutil.copy2(old_disk_file, new_disk_file)
                except OSError:
                    pass

    def _cache_key(self, path: Path, size: QSize) -> str:
        return thumbnail_cache_key(path, (size.width(), size.height()))

    def _add_to_memory(self, key: str, pixmap: QPixmap):
        if len(self._memory_cache) > self._max_memory_items:
            # Simple eviction: remove random item (first)
            self._memory_cache.pop(next(iter(self._memory_cache)))
        self._memory_cache[key] = pixmap

    def _load_or_render_thumbnail(self, path: Path, size: QSize) -> Optional[QImage]:
        """Load L2 or render and persist a thumbnail inside a worker thread."""

        key = self._cache_key(path, size)
        disk_file = thumbnail_cache_file_for_key(self._disk_cache_path, key)
        if disk_file.exists():
            image = QImage(str(disk_file))
            if not image.isNull():
                emit_perf_event("thumbnail_cache_hit", tier="L2-worker", key=key)
                return image

        image = self._render_thumbnail(path, size)
        if image is None or image.isNull():
            return None
        try:
            image.save(str(disk_file), "JPEG")
        except OSError:
            pass
        return image

    def _render_thumbnail(self, path: Path, size: QSize) -> Optional[QImage]:
        started = monotonic_ms()
        if size.isEmpty() or not size.isValid():
            return None

        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
        is_video = path.suffix.lower() in video_exts
        qimage: Optional[QImage] = None
        if not is_video:
            qimage = image_loader.load_qimage(path, size)

        if qimage is None or qimage.isNull():
            pil_image = self._generator.generate(path, (size.width(), size.height()))
            if pil_image is None:
                return None
            qimage = image_loader.qimage_from_pil(pil_image)

        if qimage is None or qimage.isNull():
            emit_perf_event(
                "thumbnail_generate_failed",
                path=path,
                elapsed_ms=round(monotonic_ms() - started, 3),
                reason="empty_render",
            )
            return None

        if self._edit_service is not None and self._edit_service.sidecar_exists(path):
            stats = compute_color_statistics(qimage)
            state = self._edit_service.describe_adjustments(
                path,
                color_stats=stats,
            )
            adjustments = state.resolved_adjustments
        else:
            raw_adjustments = sidecar.load_adjustments(path)
            stats = compute_color_statistics(qimage) if raw_adjustments else None
            adjustments = sidecar.resolve_render_adjustments(
                raw_adjustments,
                color_stats=stats,
            )

        if adjustments:
            qimage = self._apply_geometry_and_crop(qimage, adjustments) or qimage
            qimage = apply_adjustments(qimage, adjustments, color_stats=stats)

        result = self._composite_canvas(qimage, size)
        if result is None or result.isNull():
            emit_perf_event(
                "thumbnail_generate_failed",
                path=path,
                elapsed_ms=round(monotonic_ms() - started, 3),
                reason="empty_composite",
            )
        return result

    def _apply_geometry_and_crop(
        self,
        image: QImage,
        adjustments: Dict[str, float],
    ) -> Optional[QImage]:
        rotate_steps = int(adjustments.get("Crop_Rotate90", 0))
        flip_h = bool(adjustments.get("Crop_FlipH", False))
        straighten = float(adjustments.get("Crop_Straighten", 0.0))
        p_vert = float(adjustments.get("Perspective_Vertical", 0.0))
        p_horz = float(adjustments.get("Perspective_Horizontal", 0.0))

        tex_crop = (
            float(adjustments.get("Crop_CX", 0.5)),
            float(adjustments.get("Crop_CY", 0.5)),
            float(adjustments.get("Crop_W", 1.0)),
            float(adjustments.get("Crop_H", 1.0)),
        )

        log_cx, log_cy, log_w, log_h = geo_utils.texture_crop_to_logical(
            tex_crop,
            rotate_steps,
        )

        w, h = image.width(), image.height()

        if (
            rotate_steps == 0
            and not flip_h
            and abs(straighten) < 1e-5
            and abs(p_vert) < 1e-5
            and abs(p_horz) < 1e-5
            and log_w >= 0.999
            and log_h >= 0.999
        ):
            return image

        if rotate_steps % 2 == 1:
            logical_aspect = float(h) / float(w) if w > 0 else 1.0
        else:
            logical_aspect = float(w) / float(h) if h > 0 else 1.0

        matrix_inv = geo_utils.build_perspective_matrix(
            vertical=p_vert,
            horizontal=p_horz,
            image_aspect_ratio=logical_aspect,
            straighten_degrees=straighten,
            rotate_steps=0,
            flip_horizontal=flip_h,
        )

        try:
            matrix = np.linalg.inv(matrix_inv)
        except np.linalg.LinAlgError:
            matrix = np.identity(3)

        qt_perspective = QTransform(
            matrix[0, 0],
            matrix[1, 0],
            matrix[2, 0],
            matrix[0, 1],
            matrix[1, 1],
            matrix[2, 1],
            matrix[0, 2],
            matrix[1, 2],
            matrix[2, 2],
        )

        t_to_norm = QTransform().scale(1.0 / w, 1.0 / h)

        t_rot = QTransform()
        t_rot.translate(0.5, 0.5)
        t_rot.rotate(rotate_steps * 90)
        t_rot.translate(-0.5, -0.5)

        t_to_ndc = QTransform().translate(-1.0, -1.0).scale(2.0, 2.0)
        t_from_ndc = QTransform().translate(0.5, 0.5).scale(0.5, 0.5)

        log_w_px = h if rotate_steps % 2 else w
        log_h_px = w if rotate_steps % 2 else h
        t_to_pixels = QTransform().scale(log_w_px, log_h_px)

        transform = t_to_norm * t_rot * t_to_ndc * qt_perspective * t_from_ndc * t_to_pixels

        crop_x_px = log_cx * log_w_px - (log_w * log_w_px * 0.5)
        crop_y_px = log_cy * log_h_px - (log_h * log_h_px * 0.5)
        crop_w_px = log_w * log_w_px
        crop_h_px = log_h * log_h_px

        t_final = transform * QTransform().translate(-crop_x_px, -crop_y_px)

        out_w = max(1, int(round(crop_w_px)))
        out_h = max(1, int(round(crop_h_px)))

        result_img = QImage(out_w, out_h, QImage.Format.Format_ARGB32_Premultiplied)
        result_img.fill(Qt.transparent)

        painter = QPainter(result_img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        painter.setTransform(t_final)
        painter.drawImage(0, 0, image)
        painter.end()

        return result_img

    def _composite_canvas(self, image: QImage, size: QSize) -> QImage:
        canvas = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        scaled = image.scaled(
            size,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        target_rect = canvas.rect()
        source_rect = scaled.rect()
        if source_rect.width() > target_rect.width():
            diff = source_rect.width() - target_rect.width()
            left = diff // 2
            right = diff - left
            source_rect.adjust(left, 0, -right, 0)
        if source_rect.height() > target_rect.height():
            diff = source_rect.height() - target_rect.height()
            top = diff // 2
            bottom = diff - top
            source_rect.adjust(0, top, 0, -bottom)
        painter.drawImage(target_rect, scaled, source_rect)
        painter.end()
        return canvas
