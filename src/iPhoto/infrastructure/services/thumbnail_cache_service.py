import os
import shutil
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Deque, Dict, Iterable, Optional, Set

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform

from iPhoto.application.ports import EditServicePort
from iPhoto.core import geo_utils
from iPhoto.core.color_resolver import compute_color_statistics
from iPhoto.core.image_filters import apply_adjustments
from iPhoto.infrastructure.services.performance_events import (
    emit_perf_event,
    monotonic_ms,
    perf_logging_enabled,
)
from iPhoto.infrastructure.services.thumbnail_cache_keys import (
    thumbnail_cache_file_for_key,
    thumbnail_cache_key,
)
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.io import sidecar
from iPhoto.utils import image_loader


class ThumbnailWorkerSignals(QObject):
    """Signals emitted by thumbnail generation workers."""

    result = Signal(Path, QSize, QImage, int)
    failed = Signal(Path, QSize, str, int)


class ThumbnailGenerationTask(QRunnable):
    """Background task to generate a thumbnail."""

    def __init__(
        self,
        renderer,
        path: Path,
        size: QSize,
        signals: ThumbnailWorkerSignals,
        generation: int,
    ):
        super().__init__()
        self._renderer = renderer
        self._path = path
        self._size = size
        self._signals = signals
        self._generation = int(generation)

    def run(self):
        try:
            # Generate logic (CPU intensive)
            qimg = self._renderer(self._path, self._size)
            if qimg is not None and not qimg.isNull():
                # Emit result back to main thread
                self._signals.result.emit(self._path, self._size, qimg, self._generation)
            else:
                self._signals.failed.emit(
                    self._path,
                    self._size,
                    "empty_render",
                    self._generation,
                )
        except Exception:
            self._signals.failed.emit(self._path, self._size, "exception", self._generation)

class ThumbnailCacheService(QObject):
    """
    Manages thumbnail caching (Memory + Disk) and asynchronous generation.
    """

    thumbnailReady = Signal(Path)

    def __init__(self, disk_cache_path: Path, memory_limit_mb: int | None = None):
        super().__init__()
        self._disk_cache_path = disk_cache_path
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._generator = PillowThumbnailGenerator()
        self._edit_service: EditServicePort | None = None

        self._memory_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._memory_bytes: Dict[str, int] = {}
        self._memory_used_bytes = 0
        self._memory_limit_bytes = self._resolve_memory_limit(memory_limit_mb)
        self._pinned_keys: Set[str] = set()

        self._pending_tasks: Set[str] = set()
        self._pending_generations: Dict[str, int] = {}
        self._queued_tasks: Dict[str, tuple[Path, QSize, str, int]] = {}
        self._priority_queues: dict[str, Deque[str]] = {
            "visible": deque(),
            "normal": deque(),
            "low": deque(),
        }
        self._active_tasks = 0
        self._max_active_jobs = 2
        self._failure_cooldown_seconds = 60.0
        self._failure_until: Dict[str, float] = {}
        self._is_shutting_down = False
        self._current_generation = 0
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(self._max_active_jobs)

    def shutdown(self):
        """Prevents new tasks from being submitted and clears pending logic."""
        self._is_shutting_down = True
        self._pending_tasks.clear()
        self._pending_generations.clear()
        self._queued_tasks.clear()
        for queue in self._priority_queues.values():
            queue.clear()
        self._thread_pool.clear()
        self._active_tasks = 0

    def set_disk_cache_path(self, disk_cache_path: Path) -> None:
        self._is_shutting_down = False
        if self._disk_cache_path == disk_cache_path:
            return
        self._disk_cache_path = disk_cache_path
        self._disk_cache_path.mkdir(parents=True, exist_ok=True)
        self._memory_cache.clear()
        self._memory_bytes.clear()
        self._memory_used_bytes = 0
        self._pinned_keys.clear()
        self._pending_tasks.clear()
        self._pending_generations.clear()
        self._queued_tasks.clear()
        for queue in self._priority_queues.values():
            queue.clear()
        self._failure_until.clear()

    def set_edit_service(self, edit_service: EditServicePort | None) -> None:
        """Bind the current edit surface used for thumbnail rendering."""

        self._edit_service = edit_service

    def peek_full_thumbnail(self, path: Path, size: QSize) -> Optional[QPixmap]:
        """Return an in-memory thumbnail without touching disk or starting work."""

        if self._is_shutting_down:
            return None

        key = self._cache_key(path, size)
        if key in self._memory_cache:
            self._memory_cache.move_to_end(key)
            emit_perf_event("thumbnail_cache_hit", tier="L1", key=key)
            return self._memory_cache[key]
        return None

    def has_full_thumbnail(self, path: Path, size: QSize) -> bool:
        """Return whether the full thumbnail is resident without touching its LRU state."""

        if self._is_shutting_down:
            return False
        return self._cache_key(path, size) in self._memory_cache

    def get_thumbnail(
        self,
        path: Path,
        size: QSize,
        *,
        priority: str = "normal",
    ) -> Optional[QPixmap]:
        """Compatibility API: memory-only lookup followed by asynchronous request."""

        pixmap = self.peek_full_thumbnail(path, size)
        if pixmap is not None:
            return pixmap
        self.request_many([(path, size, priority)], generation=self._current_generation)
        return None

    def request_many(
        self,
        requests: Iterable[tuple[Path, QSize, str]],
        *,
        generation: int,
    ) -> None:
        """Queue deduplicated L2/decode requests for one viewport generation."""

        if self._is_shutting_down:
            return
        self._current_generation = max(self._current_generation, int(generation))
        for path, size, priority in requests:
            key = self._cache_key(path, size)
            if key in self._memory_cache:
                continue
            if key in self._pending_tasks:
                self._pending_generations[key] = max(
                    self._pending_generations.get(key, 0),
                    int(generation),
                )
                queued = self._queued_tasks.get(key)
                if queued is not None:
                    queued_path, queued_size, queued_priority, queued_generation = queued
                    next_priority = (
                        priority
                        if self._priority_rank(priority) < self._priority_rank(queued_priority)
                        else queued_priority
                    )
                    self._queued_tasks[key] = (
                        queued_path,
                        queued_size,
                        next_priority,
                        max(queued_generation, int(generation)),
                    )
                    if next_priority != queued_priority:
                        self._priority_queues[next_priority].appendleft(key)
                continue
            if self._failure_until.get(key, 0.0) > time.monotonic():
                continue
            self._queue_generation(
                path,
                size,
                priority=priority,
                generation=int(generation),
            )

    def reconcile_demand(
        self,
        *,
        visible_paths: Iterable[Path],
        hot_paths: Iterable[Path],
        size: QSize,
        generation: int,
    ) -> None:
        """Replace queued work with the latest visible/hot thumbnail demand."""

        visible = list(dict.fromkeys(Path(path) for path in visible_paths))
        visible_set = set(visible)
        hot = [
            Path(path)
            for path in dict.fromkeys(Path(path) for path in hot_paths)
            if Path(path) not in visible_set
        ]
        desired_keys = {
            self._cache_key(path, size)
            for path in [*visible, *hot]
        }
        record_perf = perf_logging_enabled()
        pending_before = set(self._pending_tasks) if record_perf else set()
        resident = len(desired_keys.intersection(self._memory_cache)) if record_perf else 0
        self._current_generation = max(self._current_generation, int(generation))
        self.pin_visible(visible, size)

        drop_keys = set(self._queued_tasks) - desired_keys
        for key in drop_keys:
            self._queued_tasks.pop(key, None)
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)
        if drop_keys:
            for priority, queue in self._priority_queues.items():
                self._priority_queues[priority] = deque(
                    key for key in queue if key not in drop_keys
                )

        self.request_many(
            ((path, size, "visible") for path in visible),
            generation=generation,
        )
        self.request_many(
            ((path, size, "normal") for path in hot),
            generation=generation,
        )
        if record_perf:
            emit_perf_event(
                "thumbnail_demand_reconciled",
                generation=generation,
                visible=len(visible),
                hot=len(hot),
                requested=len(
                    (set(self._pending_tasks) - pending_before).intersection(desired_keys)
                ),
                resident=resident,
                canceled=len(drop_keys),
                queued=len(self._queued_tasks),
                active=self._active_tasks,
            )
        self._drain_generation_queue()

    def pin_visible(self, paths: Iterable[Path], size: QSize) -> None:
        """Keep current visible full thumbnails resident until the next viewport."""

        self._pinned_keys = {self._cache_key(path, size) for path in paths}

    def cancel_stale(self, generation: int) -> None:
        """Drop queued work older than *generation*; active workers self-discard on delivery."""

        self._current_generation = max(self._current_generation, int(generation))
        drop_keys = {
            key
            for key, (_path, _size, _priority, queued_generation) in self._queued_tasks.items()
            if queued_generation < self._current_generation
        }
        for key in drop_keys:
            self._queued_tasks.pop(key, None)
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)
        if drop_keys:
            for priority, queue in self._priority_queues.items():
                self._priority_queues[priority] = deque(
                    key for key in queue if key not in drop_keys
                )

    def cancel_pending_except(self, paths: Set[Path], size: QSize) -> None:
        """Cancel queued thumbnail work except for *paths* at *size*."""

        keep_keys = {self._cache_key(path, size) for path in paths}
        drop_keys = set(self._queued_tasks) - keep_keys
        for key in drop_keys:
            self._queued_tasks.pop(key, None)
            self._pending_tasks.discard(key)
            self._pending_generations.pop(key, None)
        if drop_keys:
            for priority, queue in self._priority_queues.items():
                self._priority_queues[priority] = deque(
                    key for key in queue if key not in drop_keys
                )

    def _queue_generation(
        self,
        path: Path,
        size: QSize,
        *,
        priority: str,
        generation: int = 0,
    ) -> None:
        priority = priority if priority in self._priority_queues else "normal"
        key = self._cache_key(path, size)
        self._pending_tasks.add(key)
        self._pending_generations[key] = max(
            self._pending_generations.get(key, 0),
            int(generation),
        )
        self._queued_tasks[key] = (path, size, priority, int(generation))
        self._priority_queues[priority].append(key)
        self._drain_generation_queue()

    def _drain_generation_queue(self) -> None:
        while not self._is_shutting_down and self._active_tasks < self._max_active_jobs:
            next_item = self._pop_next_generation()
            if next_item is None:
                return
            key, path, size, generation = next_item
            self._active_tasks += 1
            self._start_generation(key, path, size, generation)

    def _pop_next_generation(self) -> tuple[str, Path, QSize, int] | None:
        for priority in ("visible", "normal", "low"):
            queue = self._priority_queues[priority]
            while queue:
                key = queue.popleft()
                spec = self._queued_tasks.pop(key, None)
                if spec is None:
                    continue
                path, size, _priority, generation = spec
                return key, path, size, generation
        return None

    def _start_generation(self, key: str, path: Path, size: QSize, generation: int):
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
        worker = ThumbnailGenerationTask(
            self._load_or_render_thumbnail,
            path,
            size,
            worker_signals,
            generation,
        )
        self._thread_pool.start(worker)

    def _handle_generation_result(
        self,
        path: Path,
        size: QSize,
        image: QImage,
        generation: int = 0,
    ):
        # Back on main thread
        if not image.isNull():
            key = self._cache_key(path, size)
            self._pending_tasks.discard(key)
            desired_generation = self._pending_generations.pop(key, generation)
            self._failure_until.pop(key, None)
            self._active_tasks = max(0, self._active_tasks - 1)

            if self._is_shutting_down or desired_generation < self._current_generation:
                emit_perf_event(
                    "thumbnail_result_discarded",
                    path=path,
                    generation=desired_generation,
                    current_generation=self._current_generation,
                )
                self._drain_generation_queue()
                return

            pixmap = QPixmap.fromImage(image)
            self._add_to_memory(key, pixmap)
            emit_perf_event(
                "thumbnail_generate_finished",
                path=path,
                width=size.width(),
                height=size.height(),
                pending=len(self._pending_tasks),
            )
            self.thumbnailReady.emit(path)
            self._drain_generation_queue()

    def _handle_generation_failure(
        self,
        path: Path,
        size: QSize,
        reason: str,
        generation: int = 0,
    ) -> None:
        key = self._cache_key(path, size)
        self._pending_tasks.discard(key)
        desired_generation = self._pending_generations.pop(key, generation)
        self._queued_tasks.pop(key, None)
        self._active_tasks = max(0, self._active_tasks - 1)
        if (
            not self._is_shutting_down
            and desired_generation > generation
            and desired_generation >= self._current_generation
        ):
            self._queue_generation(
                path,
                size,
                priority="visible",
                generation=desired_generation,
            )
            return
        self._failure_until[key] = time.monotonic() + self._failure_cooldown_seconds
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
            self._memory_used_bytes = max(
                0,
                self._memory_used_bytes - self._memory_bytes.pop(key, 0),
            )
        self._failure_until.pop(key, None)
        self._pending_tasks.discard(key)
        self._pending_generations.pop(key, None)
        self._queued_tasks.pop(key, None)
        self._pinned_keys.discard(key)

        disk_file = thumbnail_cache_file_for_key(self._disk_cache_path, key)
        if disk_file.exists():
            try:
                disk_file.unlink()
            except OSError:
                pass

    def remap_album_paths(
        self,
        old_root: Path,
        new_root: Path,
        *,
        size: QSize | None = None,
    ) -> None:
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
                self._add_to_memory(new_key, self._memory_cache[old_key])

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
        old_bytes = self._memory_bytes.pop(key, 0)
        self._memory_used_bytes = max(0, self._memory_used_bytes - old_bytes)
        bytes_per_pixel = max(1, (int(pixmap.depth()) + 7) // 8)
        estimated_bytes = max(
            1,
            int(pixmap.width()) * int(pixmap.height()) * bytes_per_pixel,
        )
        self._memory_cache[key] = pixmap
        self._memory_cache.move_to_end(key)
        self._memory_bytes[key] = estimated_bytes
        self._memory_used_bytes += estimated_bytes
        while self._memory_used_bytes > self._memory_limit_bytes and len(self._memory_cache) > 1:
            evicted_key = next(
                (
                    candidate
                    for candidate in self._memory_cache
                    if candidate not in self._pinned_keys and candidate != key
                ),
                None,
            )
            if evicted_key is None:
                break
            self._memory_cache.pop(evicted_key, None)
            self._memory_used_bytes -= self._memory_bytes.pop(evicted_key, 0)

    def _load_or_render_thumbnail(self, path: Path, size: QSize) -> Optional[QImage]:
        """Load L2 or render/write a replacement entirely on a worker thread."""

        key = self._cache_key(path, size)
        disk_file = thumbnail_cache_file_for_key(self._disk_cache_path, key)
        try:
            if disk_file.exists():
                image = image_loader.qimage_from_bytes(disk_file.read_bytes())
                if image is not None and not image.isNull():
                    emit_perf_event("thumbnail_cache_hit", tier="L2", key=key)
                    return image
        except OSError:
            pass

        image = self._render_thumbnail(path, size)
        if image is None or image.isNull():
            return None
        try:
            disk_file.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(disk_file), "JPEG")
        except OSError:
            pass
        return image

    @staticmethod
    def _resolve_memory_limit(memory_limit_mb: int | None) -> int:
        if memory_limit_mb is not None:
            return max(16, int(memory_limit_mb)) * 1024 * 1024
        physical = 512 * 1024 * 1024
        try:
            physical = int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
        except (AttributeError, OSError, ValueError):
            pass
        total_budget = max(64 * 1024 * 1024, min(512 * 1024 * 1024, physical // 10))
        return total_budget * 3 // 4

    @staticmethod
    def _priority_rank(priority: str) -> int:
        return {"visible": 0, "normal": 1, "low": 2}.get(priority, 1)

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
