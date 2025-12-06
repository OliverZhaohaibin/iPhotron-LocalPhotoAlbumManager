"""Asynchronous thumbnail rendering helpers."""

from __future__ import annotations

from collections import OrderedDict
from enum import IntEnum
import hashlib
import os
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QRunnable,
    QSize,
    QThreadPool,
    Qt,
    Signal,
)
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform

import numpy as np

from ....config import THUMBNAIL_SEEK_GUARD_SEC, WORK_DIR_NAME
from ....utils.pathutils import ensure_work_dir
from ....utils import image_loader
from ....core.image_filters import apply_adjustments
from ....core.color_resolver import compute_color_statistics
from ....io import sidecar
from .video_frame_grabber import grab_video_frame
from . import geo_utils


class ThumbnailJob(QRunnable):
    """Background task that renders a thumbnail ``QImage``."""

    def __init__(
        self,
        loader: "ThumbnailLoader",
        rel: str,
        abs_path: Path,
        size: QSize,
        stamp: int,
        cache_path: Path,
        *,
        is_image: bool,
        is_video: bool,
        still_image_time: Optional[float],
        duration: Optional[float],
    ) -> None:
        super().__init__()
        self._loader = loader
        self._rel = rel
        self._abs_path = abs_path
        self._size = size
        self._stamp = stamp
        self._cache_path = cache_path
        self._is_image = is_image
        self._is_video = is_video
        self._still_image_time = still_image_time
        self._duration = duration

    def run(self) -> None:  # pragma: no cover - executed in worker thread
        image = self._render_media()
        success = False
        if image is not None:
            success = self._write_cache(image)
        loader = getattr(self, "_loader", None)
        if loader is None:
            return

        if success:
            try:
                loader.cache_written.emit(self._cache_path)
            except AttributeError:  # pragma: no cover - dummy loader in tests
                pass
            except RuntimeError:  # pragma: no cover - race with QObject deletion
                pass

        try:
            loader._delivered.emit(
                loader._make_key(self._rel, self._size, self._stamp),
                image,
                self._rel,
            )
        except RuntimeError:  # pragma: no cover - race with QObject deletion
            pass

    def _render_media(self) -> Optional[QImage]:  # pragma: no cover - worker helper
        if self._is_video:
            return self._render_video()
        if self._is_image:
            return self._render_image()
        return None

    def _render_image(self) -> Optional[QImage]:  # pragma: no cover - worker helper
        image = image_loader.load_qimage(self._abs_path, self._size)
        if image is None:
            return None
        raw_adjustments = sidecar.load_adjustments(self._abs_path)
        stats = compute_color_statistics(image) if raw_adjustments else None
        adjustments = sidecar.resolve_render_adjustments(
            raw_adjustments,
            color_stats=stats,
        )
        
        # Apply crop before other adjustments and scaling
        if adjustments:
            image = self._apply_geometry_and_crop(image, adjustments)
            if image is None:
                return None
            image = apply_adjustments(image, adjustments, color_stats=stats)
        return self._composite_canvas(image)

    def _render_video(self) -> Optional[QImage]:  # pragma: no cover - worker helper
        image = grab_video_frame(
            self._abs_path,
            self._size,
            still_image_time=self._still_image_time,
            duration=self._duration,
        )
        if image is None:
            return None

        # Video thumbnails should also respect edits
        raw_adjustments = sidecar.load_adjustments(self._abs_path)
        stats = compute_color_statistics(image) if raw_adjustments else None
        adjustments = sidecar.resolve_render_adjustments(
            raw_adjustments,
            color_stats=stats,
        )

        if adjustments:
            image = self._apply_geometry_and_crop(image, adjustments)
            if image is None:
                return None
            image = apply_adjustments(image, adjustments, color_stats=stats)

        return self._composite_canvas(image)

    def _apply_geometry_and_crop(self, image: QImage, adjustments: Dict[str, float]) -> Optional[QImage]:
        """Apply geometric transformations (rotation, perspective, straighten) and crop.
        
        This method replicates the visual result of the OpenGL viewer on the CPU.
        """
        rotate_steps = int(adjustments.get("Crop_Rotate90", 0))
        flip_h = bool(adjustments.get("Crop_FlipH", False))
        straighten = float(adjustments.get("Crop_Straighten", 0.0))
        p_vert = float(adjustments.get("Perspective_Vertical", 0.0))
        p_horz = float(adjustments.get("Perspective_Horizontal", 0.0))

        tex_crop = (
            float(adjustments.get("Crop_CX", 0.5)),
            float(adjustments.get("Crop_CY", 0.5)),
            float(adjustments.get("Crop_W", 1.0)),
            float(adjustments.get("Crop_H", 1.0))
        )
        
        # Convert texture space crop to logical space (accounting for 90-degree rotations)
        log_cx, log_cy, log_w, log_h = geo_utils.texture_crop_to_logical(tex_crop, rotate_steps)
        
        w, h = image.width(), image.height()

        # If no significant geometry changes and full crop, return original
        if (rotate_steps == 0 and not flip_h and abs(straighten) < 1e-5 and
            abs(p_vert) < 1e-5 and abs(p_horz) < 1e-5 and
            log_w >= 0.999 and log_h >= 0.999):
            return image

        # Calculate logical aspect ratio for perspective matrix
        if rotate_steps % 2 == 1:
            logical_aspect = float(h) / float(w) if w > 0 else 1.0
        else:
            logical_aspect = float(w) / float(h) if h > 0 else 1.0

        # Build perspective matrix (Inverse: Projected -> Texture)
        matrix_inv = geo_utils.build_perspective_matrix(
            vertical=p_vert,
            horizontal=p_horz,
            image_aspect_ratio=logical_aspect,
            straighten_degrees=straighten,
            rotate_steps=0, # Rotation handled separately via QTransform
            flip_horizontal=flip_h
        )

        # We need the Forward matrix (Texture -> Projected)
        try:
            matrix = np.linalg.inv(matrix_inv)
        except np.linalg.LinAlgError:
            matrix = np.identity(3)

        # Convert to QTransform
        # QTransform expects elements in a specific order that corresponds to
        # the transpose of the standard matrix math convention used by numpy.
        # h11=m00, h12=m10, h13=m20
        # h21=m01, h22=m11, h23=m21
        # h31=m02, h32=m12, h33=m22
        qt_perspective = QTransform(
            matrix[0, 0], matrix[1, 0], matrix[2, 0],
            matrix[0, 1], matrix[1, 1], matrix[2, 1],
            matrix[0, 2], matrix[1, 2], matrix[2, 2]
        )
        
        # Construct transformation chain
        # 1. Normalize Pixels -> [0, 1]
        t_to_norm = QTransform().scale(1.0 / w, 1.0 / h)
        
        # 2. Apply 90-degree Rotation Steps around center (0.5, 0.5)
        # QTransform methods pre-multiply (apply to left), so we build in reverse order of application.
        # Desired: T(-0.5) * R * T(0.5) (applied to point p: p * T(-0.5) * R * T(0.5))
        # Construction: T(0.5).rotate().translate(-0.5)
        t_rot = QTransform()
        t_rot.translate(0.5, 0.5)
        t_rot.rotate(rotate_steps * 90)
        t_rot.translate(-0.5, -0.5)
        
        # 3. Normalize to [-1, 1] for Perspective Matrix
        # Desired: p * S(2) * T(-1) = 2p - 1.
        # QTransform builder applies operations in reverse (First().Second() -> Second * First).
        # We want S * T matrix. So T().S().
        t_to_ndc = QTransform().translate(-1.0, -1.0).scale(2.0, 2.0)
        
        # 4. Denormalize from [-1, 1] to [0, 1]
        # Desired: p * S(0.5) * T(0.5) = 0.5p + 0.5.
        # Call Order: T(0.5).S(0.5).
        t_from_ndc = QTransform().translate(0.5, 0.5).scale(0.5, 0.5)
        
        # 5. Scale to Logical Pixels
        log_w_px = h if rotate_steps % 2 else w
        log_h_px = w if rotate_steps % 2 else h
        t_to_pixels = QTransform().scale(log_w_px, log_h_px)

        # Compose full transform:
        # Texture -> Norm -> Rotated -> NDC -> Perspective -> NDC -> Norm -> Pixels
        transform = t_to_norm * t_rot * t_to_ndc * qt_perspective * t_from_ndc * t_to_pixels

        # Calculate crop rectangle in Logical Pixels
        crop_x_px = log_cx * log_w_px - (log_w * log_w_px * 0.5)
        crop_y_px = log_cy * log_h_px - (log_h * log_h_px * 0.5)
        crop_w_px = log_w * log_w_px
        crop_h_px = log_h * log_h_px

        # Shift so crop top-left is at (0, 0)
        t_final = transform * QTransform().translate(-crop_x_px, -crop_y_px)

        # Determine output size
        out_w = max(1, int(round(crop_w_px)))
        out_h = max(1, int(round(crop_h_px)))

        # Render
        result_img = QImage(out_w, out_h, QImage.Format.Format_ARGB32_Premultiplied)
        result_img.fill(Qt.transparent)

        painter = QPainter(result_img)
        # Use high quality rendering
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        painter.setTransform(t_final)
        painter.drawImage(0, 0, image)
        painter.end()
        
        return result_img

    def _seek_targets(self) -> list[Optional[float]]:
        """Return seek offsets for video thumbnails with guard rails."""

        if not self._is_video:
            return [None]

        targets: list[Optional[float]] = []
        seen: set[Optional[float]] = set()

        def add(candidate: Optional[float]) -> None:
            if candidate is None:
                key: Optional[float] = None
                value: Optional[float] = None
            else:
                value = max(candidate, 0.0)
                if self._duration and self._duration > 0:
                    guard = min(
                        max(THUMBNAIL_SEEK_GUARD_SEC, self._duration * 0.1),
                        self._duration / 2.0,
                    )
                    max_seek = max(self._duration - guard, 0.0)
                    if value > max_seek:
                        value = max_seek
                key = value
            if key in seen:
                return
            seen.add(key)
            targets.append(value)

        if self._still_image_time is not None:
            add(self._still_image_time)
        elif self._duration is not None and self._duration > 0:
            add(self._duration / 2.0)
        add(None)
        return targets

    def _composite_canvas(self, image: QImage) -> QImage:  # pragma: no cover - worker helper
        canvas = QImage(self._size, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        scaled = image.scaled(
            self._size,
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

    def _write_cache(self, canvas: QImage) -> bool:  # pragma: no cover - worker helper
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
            if canvas.save(str(tmp_path), "PNG"):
                ThumbnailLoader._safe_unlink(self._cache_path)
                try:
                    tmp_path.replace(self._cache_path)
                    return True
                except OSError:
                    tmp_path.unlink(missing_ok=True)
            else:  # pragma: no cover - Qt returns False on IO errors
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


class ThumbnailLoader(QObject):
    """Asynchronous thumbnail renderer with disk and memory caching."""

    # ``Path`` is used for the album root argument to keep the public signal in
    # sync with slots that expect :class:`Path` instances.  This mirrors the
    # rest of the GUI layer and prevents Nuitka from flagging the connection as
    # type-unsafe during compilation.
    ready = Signal(Path, str, QPixmap)
    cache_written = Signal(Path)
    _delivered = Signal(object, object, str)

    class Priority(IntEnum):
        """Simple priority values recognised by the loader."""

        LOW = -1
        NORMAL = 0
        VISIBLE = 1

    def __init__(self, parent: Optional[QObject] = None) -> None:
        if parent is None:
            parent = QCoreApplication.instance()
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._video_pool = QThreadPool(self)
        global_max = self._pool.maxThreadCount()
        if global_max <= 0:
            global_max = os.cpu_count() or 4
        video_threads = max(1, min(max(global_max // 2, 1), 4))
        self._video_pool.setMaxThreadCount(video_threads)
        self._album_root: Optional[Path] = None
        self._album_root_str: Optional[str] = None
        self._memory: Dict[Tuple[str, str, int, int, int], QPixmap] = {}
        self._pending: Set[Tuple[str, str, int, int, int]] = set()
        self._failures: Set[Tuple[str, str, int, int, int]] = set()
        self._missing: Set[Tuple[str, str, int, int]] = set()
        self._video_queue: Dict[
            int, OrderedDict[Tuple[str, str, int, int, int], ThumbnailJob]
        ] = {
            self.Priority.VISIBLE: OrderedDict(),
            self.Priority.NORMAL: OrderedDict(),
            self.Priority.LOW: OrderedDict(),
        }
        self._video_queue_lookup: Dict[
            Tuple[str, str, int, int, int], int
        ] = {}
        self._delivered.connect(self._handle_result)

    def shutdown(self) -> None:
        """Stop background workers so the interpreter can exit cleanly."""

        # Drop any queued-but-not-yet-started video jobs to avoid launching new
        # work while the application is shutting down.  We intentionally leave
        # the in-memory caches untouched so that, if shutdown is cancelled, the
        # loader can continue without recomputing every thumbnail.
        for queue in self._video_queue.values():
            queue.clear()
        self._video_queue_lookup.clear()

        # ``QThreadPool.clear()`` prevents additional ``QRunnable`` instances
        # from starting, and ``waitForDone()`` blocks until active workers
        # finish.  Calling both ensures the dedicated video pool no longer owns
        # any threads by the time Qt begins tearing down application state.
        self._video_pool.clear()
        self._video_pool.waitForDone()

        # ``ThumbnailLoader`` also submits still-image work to the global pool.
        # Other subsystems might share that pool, so we avoid clearing the
        # queue.  Waiting is still safe because Qt tracks active references and
        # returns immediately when no thumbnail jobs are in flight.
        self._pool.waitForDone()

    def reset_for_album(self, root: Path) -> None:
        if self._album_root and self._album_root == root:
            return
        self._album_root = root
        self._album_root_str = str(root.resolve())
        self._memory.clear()
        self._pending.clear()
        self._failures.clear()
        self._missing.clear()
        for queue in self._video_queue.values():
            queue.clear()
        self._video_queue_lookup.clear()
        try:
            work_dir = ensure_work_dir(root, WORK_DIR_NAME)
            (work_dir / "thumbs").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def request(
        self,
        rel: str,
        path: Path,
        size: QSize,
        *,
        is_image: bool,
        is_video: bool = False,
        still_image_time: Optional[float] = None,
        duration: Optional[float] = None,
        priority: "ThumbnailLoader.Priority" = Priority.NORMAL,
    ) -> Optional[QPixmap]:
        if self._album_root is None or self._album_root_str is None:
            return None
        base_key = self._base_key(rel, size)
        if base_key in self._missing:
            return None
        if not is_image and not is_video:
            return None
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            self._missing.add(base_key)
            return None
        stamp_ns = getattr(stat_result, "st_mtime_ns", None)
        if stamp_ns is None:
            stamp_ns = int(stat_result.st_mtime * 1_000_000_000)

        # Ensure that edits stored in sidecar files trigger a cache refresh by
        # mixing the sidecar timestamp into the cache key.
        sidecar_path = sidecar.sidecar_path_for_asset(path)
        if sidecar_path.exists():
            try:
                sidecar_stat = sidecar_path.stat()
                sidecar_ns = getattr(sidecar_stat, "st_mtime_ns", None)
                if sidecar_ns is None:
                    sidecar_ns = int(sidecar_stat.st_mtime * 1_000_000_000)
                stamp_ns = max(stamp_ns, sidecar_ns)
            except OSError:
                pass

        stamp = int(stamp_ns)
        key = self._make_key(rel, size, stamp)
        cached = self._memory.get(key)
        if cached is not None:
            return cached
        if key in self._failures:
            return None
        cache_path = self._cache_path(rel, size, stamp)
        if cache_path.exists():
            pixmap = QPixmap(str(cache_path))
            if not pixmap.isNull():
                # Print the cached thumbnail path to help debugging the
                # Location view while reusing disk-stored thumbnails.
                print(f"[ThumbnailLoader] Cached thumbnail hit: {cache_path}")
                self._memory[key] = pixmap
                return pixmap
            self._safe_unlink(cache_path)
        if key in self._pending:
            return None
        job = ThumbnailJob(
            self,
            rel,
            path,
            size,
            stamp,
            cache_path,
            is_image=is_image,
            is_video=is_video,
            still_image_time=still_image_time,
            duration=duration,
        )
        if is_video:
            self._queue_video_job(key, job, priority)
            self._drain_video_queue()
        else:
            self._start_job(job, key, self._pool)
        return None

    def _base_key(self, rel: str, size: QSize) -> Tuple[str, str, int, int]:
        assert self._album_root_str is not None
        return (self._album_root_str, rel, size.width(), size.height())

    def _make_key(self, rel: str, size: QSize, stamp: int) -> Tuple[str, str, int, int, int]:
        base = self._base_key(rel, size)
        return (*base, stamp)

    def _cache_path(self, rel: str, size: QSize, stamp: int) -> Path:
        assert self._album_root is not None
        digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()
        filename = f"{digest}_{stamp}_{size.width()}x{size.height()}.png"
        return self._album_root / WORK_DIR_NAME / "thumbs" / filename

    def _handle_result(
        self,
        key: Tuple[str, str, int, int, int],
        image: Optional[QImage],
        rel: str,
    ) -> None:
        self._pending.discard(key)
        if image is None:
            self._failures.add(key)
            self._drain_video_queue()
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._failures.add(key)
            self._drain_video_queue()
            return
        base = key[:-1]
        obsolete = [existing for existing in self._memory if existing[:-1] == base and existing != key]
        for existing in obsolete:
            self._memory.pop(existing, None)
            if self._album_root is not None:
                _, _, width, height, stale_stamp = existing
                stale_size = QSize(width, height)
                stale_path = self._cache_path(rel, stale_size, stale_stamp)
                self._safe_unlink(stale_path)
        self._memory[key] = pixmap
        if self._album_root is not None:
            self.ready.emit(self._album_root, rel, pixmap)
        self._drain_video_queue()

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            try:
                path.rename(path.with_suffix(path.suffix + ".stale"))
            except OSError:
                pass
        except OSError:
            pass

    # ------------------------------------------------------------------
    def invalidate(self, rel: str) -> None:
        """Remove cached thumbnails associated with *rel*."""

        if self._album_root is None:
            return
        to_remove = [key for key in self._memory if key[1] == rel]
        for key in to_remove:
            pixmap = self._memory.pop(key, None)
            if pixmap is not None:
                del pixmap
            _, _, width, height, stamp = key
            size = QSize(width, height)
            cache_path = self._cache_path(rel, size, stamp)
            self._safe_unlink(cache_path)
        self._pending = {key for key in self._pending if key[1] != rel}
        self._failures = {key for key in self._failures if key[1] != rel}
        self._missing = {key for key in self._missing if key[1] != rel}
        obsolete_lookup = [key for key, queue in self._video_queue_lookup.items() if key[1] == rel]
        for key in obsolete_lookup:
            priority = self._video_queue_lookup.pop(key, None)
            if priority is None:
                continue
            queue = self._video_queue.get(priority)
            if queue is not None:
                queue.pop(key, None)

        if self._album_root is not None:
            # Aggressively clean up disk cache for this relative path.
            # This ensures that even if the source file timestamp hasn't changed
            # (e.g. only the sidecar was edited), we force a regeneration.
            try:
                digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()
                thumbs_dir = self._album_root / WORK_DIR_NAME / "thumbs"
                if thumbs_dir.exists():
                    # Filename format: {digest}_{stamp}_{w}x{h}.png
                    for file_path in thumbs_dir.glob(f"{digest}_*.png"):
                        self._safe_unlink(file_path)
            except Exception:
                # Don't let disk errors crash the UI during invalidation
                pass

    def _queue_video_job(
        self,
        key: Tuple[str, str, int, int, int],
        job: ThumbnailJob,
        priority: "ThumbnailLoader.Priority",
    ) -> None:
        if key in self._pending:
            return
        existing_priority = self._video_queue_lookup.get(key)
        if existing_priority is not None:
            if priority > existing_priority:
                queue = self._video_queue[existing_priority]
                existing_job = queue.pop(key, None)
                if existing_job is not None:
                    self._enqueue_video_job(key, existing_job, priority)
            return
        self._enqueue_video_job(key, job, priority)

    def _enqueue_video_job(
        self,
        key: Tuple[str, str, int, int, int],
        job: ThumbnailJob,
        priority: "ThumbnailLoader.Priority",
    ) -> None:
        queue = self._video_queue.get(priority)
        if queue is None:
            queue = OrderedDict()
            self._video_queue[priority] = queue
        queue[key] = job
        self._video_queue_lookup[key] = priority

    def _start_job(
        self,
        job: ThumbnailJob,
        key: Tuple[str, str, int, int, int],
        pool: QThreadPool,
    ) -> None:
        self._pending.add(key)
        pool.start(job)

    def _drain_video_queue(self) -> None:
        if self._video_pool is None:
            return
        if self._video_pool.activeThreadCount() >= self._video_pool.maxThreadCount():
            return
        while self._video_pool.activeThreadCount() < self._video_pool.maxThreadCount():
            next_job = self._take_next_video_job()
            if next_job is None:
                break
            key, job = next_job
            self._start_job(job, key, self._video_pool)

    def _take_next_video_job(
        self,
        ) -> Optional[Tuple[Tuple[str, str, int, int, int], ThumbnailJob]]:
        for priority in (self.Priority.VISIBLE, self.Priority.NORMAL, self.Priority.LOW):
            queue = self._video_queue.get(priority)
            if not queue:
                continue
            key, job = queue.popitem(last=False)
            self._video_queue_lookup.pop(key, None)
            return key, job
        return None
