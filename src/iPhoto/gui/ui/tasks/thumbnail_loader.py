"""Asynchronous thumbnail rendering helpers."""

from __future__ import annotations

from collections import OrderedDict, deque
from enum import IntEnum
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

try:
    import psutil
except ImportError:
    psutil = None

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


LOGGER = logging.getLogger(__name__)


def safe_unlink(path: Path) -> None:
    """
    Safely delete a file, handling permission errors gracefully.

    Attempts to delete the file at the given path. If a PermissionError occurs,
    the file is renamed with a ".stale" suffix instead. Other OSError exceptions
    (such as the file not existing or being inaccessible) are ignored.

    Parameters:
        path (Path): The path to the file to be deleted.
    """
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        try:
            path.rename(path.with_suffix(path.suffix + ".stale"))
        except OSError:
            # Ignore errors when renaming; file may be locked or already deleted.
            pass
    except OSError:
        # Ignore errors when unlinking; file may not exist or be inaccessible.
        pass


def stat_mtime_ns(stat_result: os.stat_result) -> int:
    stamp = getattr(stat_result, "st_mtime_ns", None)
    if stamp is None:
        stamp = int(stat_result.st_mtime * 1_000_000_000)
    return int(stamp)


def generate_cache_path(library_root: Path, abs_path: Path, size: QSize, stamp: int) -> Path:
    """
    Generate the file path for a cached thumbnail image.

    Args:
        library_root (Path): The root directory of the Basic Library.
        abs_path (Path): The absolute path of the media file.
        size (QSize): The desired size of the thumbnail.
        stamp (int): A timestamp or version identifier for cache invalidation.

    Returns:
        Path: The path to the cache file for the thumbnail image.
    """
    # Use absolute path for global uniqueness
    path_str = str(abs_path.resolve())
    digest = hashlib.blake2b(path_str.encode("utf-8"), digest_size=20).hexdigest()
    filename = f"{digest}_{stamp}_{size.width()}x{size.height()}.png"
    return library_root / WORK_DIR_NAME / "thumbs" / filename


class ThumbnailJob(QRunnable):
    """Background task that renders a thumbnail ``QImage``."""

    def __init__(
        self,
        loader: "ThumbnailLoader",
        rel: str,
        abs_path: Path,
        size: QSize,
        known_stamp: Optional[int],
        album_root: Path,
        library_root: Path,
        *,
        is_image: bool,
        is_video: bool,
        still_image_time: Optional[float],
        duration: Optional[float],
        cache_rel: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._loader = loader
        self._rel = rel
        self._abs_path = abs_path
        self._size = size
        self._known_stamp = known_stamp
        self._album_root = album_root
        self._library_root = library_root
        self._is_image = is_image
        self._is_video = is_video
        self._still_image_time = still_image_time
        self._duration = duration
        self._cache_rel = cache_rel
        self._job_root_str = str(album_root.resolve())

    def _make_local_key(self, stamp: int) -> Tuple[str, str, int, int, int]:
        """Generate a cache key using the fixed job root string."""
        return (
            self._job_root_str,
            self._rel,
            self._size.width(),
            self._size.height(),
            stamp,
        )

    def run(self) -> None:  # pragma: no cover - executed in worker thread
        # Memory Guard
        if psutil:
            mem = psutil.virtual_memory()
            if mem.percent > 80.0:
                time.sleep(0.5)

        # 1. Stat the file to get actual timestamp
        try:
            stat_result = self._abs_path.stat()
        except OSError:
            self._handle_missing()
            return

        stamp_ns = stat_mtime_ns(stat_result)

        # Check sidecar
        sidecar_path = sidecar.sidecar_path_for_asset(self._abs_path)
        try:
            if sidecar_path.exists():
                try:
                    sidecar_stat = sidecar_path.stat()
                    sidecar_ns = getattr(sidecar_stat, "st_mtime_ns", None)
                    if sidecar_ns is None:
                        sidecar_ns = int(sidecar_stat.st_mtime * 1_000_000_000)
                    stamp_ns = max(stamp_ns, sidecar_ns)
                except OSError:
                    # Ignore errors reading sidecar file; treat as if sidecar is missing or inaccessible.
                    pass
        except OSError:
            # Ignore errors when checking for sidecar existence or stat; sidecar may not exist or be inaccessible.
            pass

        actual_stamp = int(stamp_ns)

        # 2. Validation
        if self._known_stamp is not None:
            if self._known_stamp == actual_stamp:
                # Cache is valid, remove from pending jobs and exit
                self._report_valid(actual_stamp)
                return
            else:
                # Stale cache detected. Remove old file.
                old_path = generate_cache_path(self._library_root, self._abs_path, self._size, self._known_stamp)
                safe_unlink(old_path)

        # 3. Calculate Cache Path
        cache_path = generate_cache_path(self._library_root, self._abs_path, self._size, actual_stamp)

        image: Optional[QImage] = None
        loaded_from_cache = False

        try:
            cache_exists = cache_path.exists()
        except OSError:
            cache_exists = False
        if cache_exists:
            image = QImage(str(cache_path))
            if not image.isNull():
                loaded_from_cache = True
            else:
                safe_unlink(cache_path)
                image = None

        if image is None:
            rel_for_path = self._cache_rel if self._cache_rel is not None else self._rel
            try:
                image = self._render_media()
            except (OSError, ValueError, np.linalg.LinAlgError):
                LOGGER.exception("ThumbnailJob failed for %s (rel=%s)", self._abs_path, rel_for_path)
                loader = getattr(self, "_loader", None)
                if loader:
                    try:
                        loader._delivered.emit(
                            self._make_local_key(0),
                            None,
                            self._rel,
                        )
                    except RuntimeError:
                        pass
                return

        success = False
        if image is not None:
            if not loaded_from_cache:
                success = self._write_cache(image, cache_path)
            else:
                # Cache hit, so it's already written.
                success = True

        loader = getattr(self, "_loader", None)
        if loader is None:
            return

        if success and not loaded_from_cache:
            try:
                loader.cache_written.emit(cache_path)
            except AttributeError:  # pragma: no cover - dummy loader in tests
                pass
            except RuntimeError:
                # pragma: no cover - race with QObject deletion
                pass

        try:
            loader._delivered.emit(
                self._make_local_key(actual_stamp),
                image,
                self._rel,
            )
        except RuntimeError:  # pragma: no cover - race with QObject deletion
            pass

    def _handle_missing(self) -> None:
        loader = getattr(self, "_loader", None)
        if loader:
            try:
                # Use 0 as stamp for missing files, though the loader will just use the base key
                key = self._make_local_key(0)
                loader._delivered.emit(key, None, self._rel)
            except RuntimeError:  # pragma: no cover - race with QObject deletion
                pass

    def _report_valid(self, stamp: int) -> None:
        """Inform the loader that the existing cache is still valid."""
        loader = getattr(self, "_loader", None)
        if loader:
            try:
                loader._validation_success.emit(self._make_local_key(stamp))
            except RuntimeError:
                # pragma: no cover - race with QObject deletion
                pass
            except AttributeError:
                # The loader may have been deleted or not fully initialized; safe to ignore.
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
        """
        Apply geometric transformations (rotation, perspective, straighten) and crop to the image
        to replicate the OpenGL viewer's visual result on the CPU.

        Args:
            image (QImage): The input image to transform.
            adjustments (Dict[str, float]): Dictionary of geometric adjustment parameters.

        Returns:
            Optional[QImage]: The transformed and cropped image, or None if the operation fails.
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
        
        log_cx, log_cy, log_w, log_h = geo_utils.texture_crop_to_logical(tex_crop, rotate_steps)
        
        w, h = image.width(), image.height()

        if (rotate_steps == 0 and not flip_h and abs(straighten) < 1e-5 and
            abs(p_vert) < 1e-5 and abs(p_horz) < 1e-5 and
            log_w >= 0.999 and log_h >= 0.999):
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
            flip_horizontal=flip_h
        )

        try:
            matrix = np.linalg.inv(matrix_inv)
        except np.linalg.LinAlgError:
            matrix = np.identity(3)

        qt_perspective = QTransform(
            matrix[0, 0], matrix[1, 0], matrix[2, 0],
            matrix[0, 1], matrix[1, 1], matrix[2, 1],
            matrix[0, 2], matrix[1, 2], matrix[2, 2]
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

    def _seek_targets(self) -> list[Optional[float]]:
        """
        Return a list of seek offsets (in seconds) for video thumbnails, applying guard rails
        to avoid seeking too close to the start or end of the video. For non-video files,
        returns a list containing a single None value.
        """
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

    def _write_cache(self, canvas: QImage, path: Path) -> bool:  # pragma: no cover - worker helper
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            if canvas.save(str(tmp_path), "PNG"):
                safe_unlink(path)
                try:
                    tmp_path.replace(path)
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

    ready = Signal(Path, str, QPixmap)
    cache_written = Signal(Path)
    _delivered = Signal(object, object, str)
    _validation_success = Signal(object)

    class Priority(IntEnum):
        """
        Priority levels for thumbnail loading jobs.

        These values indicate the relative importance of a thumbnail request.
        - LOW: Background or prefetch requests that are not immediately needed.
        - NORMAL: Standard requests for thumbnails.
        - VISIBLE: Requests for thumbnails that are currently visible in the UI and should be prioritized.

        Note: The priority parameter is accepted in the request method, but is not currently used in the implementation.
        """
        LOW = -1
        NORMAL = 0
        VISIBLE = 1

    def __init__(self, parent: Optional[QObject] = None, library_root: Optional[Path] = None) -> None:
        if parent is None:
            parent = QCoreApplication.instance()
        super().__init__(parent)
        self._library_root: Optional[Path] = library_root
        self._pool = QThreadPool.globalInstance()
        global_max = self._pool.maxThreadCount()
        if global_max <= 0:
            global_max = os.cpu_count() or 4

        self._max_active_jobs = max(1, global_max - 1)
        self._active_jobs_count = 0

        self._album_root: Optional[Path] = None
        self._album_root_str: Optional[str] = None

        self._memory: OrderedDict[Tuple[str, str, int, int], Tuple[int, QPixmap]] = OrderedDict()
        self._max_memory_items = 500

        self._pending_deque: deque[Tuple[Tuple[str, str, int, int], ThumbnailJob]] = deque()
        self._pending_keys: Set[Tuple[str, str, int, int]] = set()

        self._failures: Set[Tuple[str, str, int, int]] = set()
        self._missing: Set[Tuple[str, str, int, int]] = set()
        self._failure_counts: Dict[Tuple[str, str, int, int], int] = {}
        self._job_specs: Dict[
            Tuple[str, str, int, int],
            Tuple[
                str,
                Path,
                QSize,
                Optional[int],
                Path,
                Path,
                bool,
                bool,
                Optional[float],
                Optional[float],
            ],
        ] = {}

        self._delivered.connect(self._handle_result)
        self._validation_success.connect(self._handle_validation_success)

    def shutdown(self) -> None:
        self._pending_deque.clear()
        self._pending_keys.clear()
        self._pool.waitForDone()

    def set_library_root(self, root: Path) -> None:
        self._library_root = root
        if root:
            try:
                work_dir = ensure_work_dir(root, WORK_DIR_NAME)
                (work_dir / "thumbs").mkdir(parents=True, exist_ok=True)
            except OSError:
                # Ignore errors if the thumbnail directory cannot be created; not fatal.
                pass

    def reset_for_album(self, root: Path) -> None:
        if self._album_root and self._album_root == root:
            return
        self._album_root = root
        self._album_root_str = str(root.resolve())
        self._memory.clear()
        self._pending_deque.clear()
        self._pending_keys.clear()
        self._failures.clear()
        self._missing.clear()
        self._failure_counts.clear()
        self._job_specs.clear()

        # Ensure the thumbnail directory exists in the library root (if set)
        if self._library_root:
            try:
                work_dir = ensure_work_dir(self._library_root, WORK_DIR_NAME)
                (work_dir / "thumbs").mkdir(parents=True, exist_ok=True)
            except OSError:
                # Ignore errors when creating the thumbnail directory; it may already exist or be inaccessible,
                # and the application can proceed without it.
                pass
        else:
            # Fallback: create in local album root if library not configured
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

        # Fallback to album_root if library_root is not set
        lib_root = self._library_root if self._library_root else self._album_root

        fixed_size = QSize(512, 512)
        base_key = self._base_key(rel, fixed_size)

        if base_key in self._missing:
            return None
        if not is_image and not is_video:
            return None

        cached_entry = self._memory.get(base_key)
        known_stamp: Optional[int] = None
        retval: Optional[QPixmap] = None

        if cached_entry:
            self._memory.move_to_end(base_key)
            known_stamp, retval = cached_entry

        if base_key in self._pending_keys:
            return retval

        if base_key in self._failures:
            return None

        job = self._create_job_from_spec(
            rel,
            path,
            fixed_size,
            known_stamp,
            self._album_root,
            lib_root,
            is_image,
            is_video,
            still_image_time,
            duration,
        )

        self._store_job_spec(
            base_key,
            rel,
            path,
            fixed_size,
            known_stamp,
            self._album_root,
            lib_root,
            is_image,
            is_video,
            still_image_time,
            duration,
        )

        self._schedule_job(base_key, job)
        return retval

    def _schedule_job(self, key: Tuple[str, str, int, int], job: ThumbnailJob) -> None:
        self._pending_deque.append((key, job))
        self._pending_keys.add(key)
        self._drain_queue()

    def _drain_queue(self) -> None:
        while self._active_jobs_count < self._max_active_jobs and self._pending_deque:
            key, job = self._pending_deque.pop() # LIFO
            if key not in self._pending_keys:
                continue

            self._start_job(job, key)

    def _start_job(
        self,
        job: ThumbnailJob,
        key: Tuple[str, str, int, int],
    ) -> None:
        self._active_jobs_count += 1
        self._pool.start(job)

    def _store_job_spec(
        self,
        base_key: Tuple[str, str, int, int],
        rel: str,
        path: Path,
        size: QSize,
        known_stamp: Optional[int],
        album_root: Path,
        library_root: Path,
        is_image: bool,
        is_video: bool,
        still_image_time: Optional[float],
        duration: Optional[float],
    ) -> None:
        self._job_specs[base_key] = (
            rel,
            path,
            size,
            known_stamp,
            album_root,
            library_root,
            is_image,
            is_video,
            still_image_time,
            duration,
        )

    def _create_job_from_spec(
        self,
        rel: str,
        abs_path: Path,
        size: QSize,
        known_stamp: Optional[int],
        album_root: Path,
        library_root: Path,
        is_image: bool,
        is_video: bool,
        still_image_time: Optional[float],
        duration: Optional[float],
    ) -> ThumbnailJob:
        return ThumbnailJob(
            self,
            rel,
            abs_path,
            size,
            known_stamp,
            album_root,
            library_root,
            is_image=is_image,
            is_video=is_video,
            still_image_time=still_image_time,
            duration=duration,
        )

    def _record_terminal_failure(
        self,
        base_key: Tuple[str, str, int, int],
        *,
        increment: bool = True,
    ) -> None:
        if increment:
            self._failure_counts[base_key] = self._failure_counts.get(base_key, 0) + 1
        self._failures.add(base_key)
        self._missing.add(base_key)

    def _base_key(self, rel: str, size: QSize) -> Tuple[str, str, int, int]:
        assert self._album_root_str is not None
        return (self._album_root_str, rel, size.width(), size.height())

    def _make_key(self, rel: str, size: QSize, stamp: int) -> Tuple[str, str, int, int, int]:
        base = self._base_key(rel, size)
        return (*base, stamp)

    def _handle_result(
        self,
        full_key: Tuple[str, str, int, int, int],
        image: Optional[QImage],
        rel: str,
    ) -> None:
        base_key = full_key[:-1]
        stamp = full_key[-1]

        self._active_jobs_count = max(0, self._active_jobs_count - 1)
        self._pending_keys.discard(base_key)
        spec = self._job_specs.get(base_key)

        if image is None:
            if self._retry_after_failure(base_key, rel, spec):
                self._drain_queue()
                return
            already_retried = self._failure_counts.get(base_key, 0) >= 1
            self._record_terminal_failure(base_key, increment=not already_retried)
            self._job_specs.pop(base_key, None)
            self._drain_queue()
            return

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            if self._retry_after_failure(base_key, rel, spec):
                self._drain_queue()
                return
            already_retried = self._failure_counts.get(base_key, 0) >= 1
            self._record_terminal_failure(base_key, increment=not already_retried)
            self._job_specs.pop(base_key, None)
            self._drain_queue()
            return

        self._failure_counts.pop(base_key, None)
        self._job_specs.pop(base_key, None)
        self._memory[base_key] = (stamp, pixmap)

        while len(self._memory) > self._max_memory_items:
            self._memory.popitem(last=False)

        if self._album_root is not None:
            self.ready.emit(self._album_root, rel, pixmap)

        self._drain_queue()

    def _handle_validation_success(self, full_key: Tuple[str, str, int, int, int]) -> None:
        base_key = full_key[:-1]
        self._active_jobs_count = max(0, self._active_jobs_count - 1)
        self._pending_keys.discard(base_key)
        self._drain_queue()



    def invalidate(self, rel: str) -> None:
        if self._album_root is None:
            return

        to_remove = [k for k in self._memory if k[1] == rel]

        for k in to_remove:
            entry = self._memory.pop(k, None)
            if entry:
                stamp, pixmap = entry
                del pixmap
                # We intentionally do not delete the disk cache here.
                # Invalidation primarily means "clear from memory and force a check".
                # If the disk file is stale, ThumbnailJob will detect the timestamp mismatch
                # and clean it up. If the disk file is still valid (e.g. invalidation triggered
                # by a false alarm or metadata update), we want to reuse it.

        self._pending_keys = {k for k in self._pending_keys if k[1] != rel}
        self._failures = {k for k in self._failures if k[1] != rel}
        self._missing = {k for k in self._missing if k[1] != rel}
        self._failure_counts = {k: v for k, v in self._failure_counts.items() if k[1] != rel}
        self._job_specs = {k: v for k, v in self._job_specs.items() if k[1] != rel}
        # Remove jobs for the invalidated rel from the pending deque to avoid zombie entries
        self._pending_deque = deque(
            (key, job) for key, job in self._pending_deque
            if key[1] != rel
        )

    def _retry_after_failure(
        self,
        base_key: Tuple[str, str, int, int],
        rel: str,
        spec: Optional[
            Tuple[
                str,
                Path,
                QSize,
                Optional[int],
                Path,
                Path,
                bool,
                bool,
                Optional[float],
                Optional[float],
            ]
        ],
    ) -> bool:
        """
        Attempt to schedule a single retry after a thumbnail rendering failure.

        A retry is only scheduled when ``spec`` is available and the given
        ``base_key`` has not already been retried (tracked via
        ``self._failure_counts``). When eligible, this method:

        * chooses the effective rel path from the stored spec (if present),
        * cleans up any cached thumbnail derived from the known stamp to avoid
          reusing corrupt data,
        * recreates a :class:`ThumbnailJob` from the stored parameters, and
        * reschedules that job via ``_schedule_job`` while persisting the spec.

        Args:
            base_key: Cache key for the job.
            rel: The rel path passed to the original request.
            spec: Stored job parameters needed to recreate the job.

        Returns:
            True if a retry was scheduled; False if no retry will occur.
        """

        if spec is None:
            return False

        attempts = self._failure_counts.get(base_key, 0)
        if attempts >= 1:
            return False

        self._failure_counts[base_key] = attempts + 1

        (
            stored_rel,
            abs_path,
            size,
            known_stamp,
            album_root,
            library_root,
            is_image,
            is_video,
            still_image_time,
            duration,
        ) = spec

        retry_rel = stored_rel if stored_rel is not None else rel
        if known_stamp is not None:
            try:
                cache_path = generate_cache_path(library_root, abs_path, size, known_stamp)
                safe_unlink(cache_path)
            except OSError:
                LOGGER.debug("Failed to cleanup cache for %s", abs_path, exc_info=True)

        retry_job = self._create_job_from_spec(
            retry_rel,
            abs_path,
            size,
            known_stamp,
            album_root,
            library_root,
            is_image,
            is_video,
            still_image_time,
            duration,
        )

        self._store_job_spec(
            base_key,
            retry_rel,
            abs_path,
            size,
            known_stamp,
            album_root,
            library_root,
            is_image,
            is_video,
            still_image_time,
            duration,
        )

        self._schedule_job(base_key, retry_job)
        return True
