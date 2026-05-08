"""Asynchronous thumbnail rendering helpers.

This module is the main entry-point for thumbnail scheduling.  The heavy
lifting has been split into sub-modules:

* :mod:`.thumbnail_cache`      – disk-cache path generation & IO
* :mod:`.thumbnail_compositor` – canvas composition (square crop)
* :mod:`.thumbnail_renderer`   – image / video rendering pipeline
* :mod:`.thumbnail_job`        – ``ThumbnailJob`` QRunnable

All public symbols are re-exported here for backward compatibility.
"""

from __future__ import annotations

from collections import OrderedDict
from enum import IntEnum
import heapq
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QSize,
    QThreadPool,
    Qt,
    Signal,
)
from PySide6.QtGui import QImage, QPixmap

from ....utils.pathutils import ensure_work_dir

# Re-exported names – keep every symbol that downstream code may import
# from ``iPhoto.gui.ui.tasks.thumbnail_loader``.
from .thumbnail_cache import safe_unlink, stat_mtime_ns, generate_cache_path  # noqa: F401
from .thumbnail_job import ThumbnailJob  # noqa: F401


LOGGER = logging.getLogger(__name__)


class ThumbnailLoader(QObject):
    """Asynchronous thumbnail renderer with disk and memory caching and priority scheduling."""

    ready = Signal(Path, str, QPixmap)
    cache_written = Signal(Path)
    _delivered = Signal(object, object, str)
    _validation_success = Signal(object)

    class Priority(IntEnum):
        """
        Priority levels for thumbnail loading jobs.

        Priority values are used in a min-heap: lower values execute first.
        - VISIBLE: Currently visible in viewport (highest priority)
        - HOT: Within 1-3 screens of visible area (scroll direction aware)
        - WARM: Within 4-8 screens of visible area
        - PREFETCH: Beyond warm range, background prefetch only
        """
        VISIBLE = 0
        HOT = 10
        WARM = 30
        PREFETCH = 80
        # Legacy aliases for backward compatibility
        LOW = 80
        NORMAL = 30

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

        # Memory cache: key = (album_root_str, rel, w, h), value = (stamp, pixmap)
        self._memory: OrderedDict[Tuple[str, str, int, int], Tuple[int, QPixmap]] = OrderedDict()
        self._max_memory_items = 500

        # Priority queue: list of (priority, sequence_num, key, job)
        # Using heapq for min-heap behavior (lowest priority first)
        self._pending_heap: List[Tuple[int, int, Tuple[str, str, int, int], ThumbnailJob]] = []
        self._pending_keys: set[Tuple[str, str, int, int]] = set()
        self._pending_priorities: Dict[Tuple[str, str, int, int], int] = {}
        self._sequence_counter = 0  # Tiebreaker for heap items with same priority

        self._failures: set[Tuple[str, str, int, int]] = set()
        self._missing: set[Tuple[str, str, int, int]] = set()
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

        # Generation counter for viewport-based cancellation
        self._generation = 0

        # Hot range tracking for cancellation
        self._hot_first = -1
        self._hot_last = -1

        self._delivered.connect(self._handle_result)
        self._validation_success.connect(self._handle_validation_success)

    def shutdown(self) -> None:
        self._pending_heap.clear()
        self._pending_keys.clear()
        self._pending_priorities.clear()
        self._job_specs.clear()
        self._pool.waitForDone()

    def set_library_root(self, root: Path) -> None:
        self._library_root = root
        if root:
            try:
                work_dir = ensure_work_dir(root)
                (work_dir / "thumbs").mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def reset_for_album(self, root: Path) -> None:
        if self._album_root and self._album_root == root:
            return
        self._album_root = root
        self._album_root_str = str(root.resolve())
        self._memory.clear()
        self._pending_heap.clear()
        self._pending_keys.clear()
        self._pending_priorities.clear()
        self._failures.clear()
        self._missing.clear()
        self._failure_counts.clear()
        self._job_specs.clear()
        self._generation = 0
        self._hot_first = -1
        self._hot_last = -1

        if self._library_root:
            try:
                work_dir = ensure_work_dir(self._library_root)
                (work_dir / "thumbs").mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        else:
            try:
                work_dir = ensure_work_dir(root)
                (work_dir / "thumbs").mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def bump_generation(self) -> int:
        """Increment the generation counter and return the new value.

        Call this when the viewport changes significantly (e.g., user scrolls).
        Jobs from previous generations will be discarded when they complete.
        """
        self._generation += 1
        LOGGER.debug("[ThumbnailLoader] generation bumped to %d", self._generation)
        return self._generation

    def set_hot_range(self, first: int, last: int) -> bool:
        """Set the hot range for priority tracking.

        Returns True if the range changed significantly.
        """
        changed = self._hot_first != first or self._hot_last != last
        self._hot_first = first
        self._hot_last = last
        return changed

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
        generation: Optional[int] = None,
        known_stamp: Optional[int] = None,
    ) -> Optional[QPixmap]:
        """Request a thumbnail for the given asset.

        Args:
            rel: Relative path of the asset
            path: Absolute path of the asset
            size: Target size for the thumbnail
            is_image: True if asset is an image
            is_video: True if asset is a video
            still_image_time: Time offset for video thumbnail
            duration: Video duration
            priority: Request priority (VISIBLE, HOT, WARM, PREFETCH)
            generation: Generation ID for cancellation tracking
            known_stamp: Pre-known stamp for fast cache hit (optional)

        Returns:
            Cached QPixmap if available, None otherwise (thumbnail will be delivered via signal)
        """
        if self._album_root is None or self._album_root_str is None:
            return None

        lib_root = self._library_root if self._library_root else self._album_root
        fixed_size = QSize(512, 512)
        base_key = self._base_key(rel, fixed_size)

        if base_key in self._missing:
            return None
        if not is_image and not is_video:
            return None

        # Check memory cache for fast hit
        cached_entry = self._memory.get(base_key)
        cached_stamp: Optional[int] = None
        retval: Optional[QPixmap] = None

        if cached_entry:
            self._memory.move_to_end(base_key)
            cached_stamp, retval = cached_entry

        # If we have a known stamp and it matches cache, return directly
        if known_stamp is not None and cached_stamp == known_stamp:
            return retval

        # If we have a cached entry, we can verify it without spawning worker
        if cached_entry is not None:
            # Schedule validation job with known stamp from cache
            if base_key not in self._pending_keys:
                self._store_job_spec(
                    base_key,
                    rel,
                    path,
                    fixed_size,
                    cached_stamp,
                    self._album_root,
                    lib_root,
                    is_image,
                    is_video,
                    still_image_time,
                    duration,
                )
                job = self._create_job_from_spec(
                    rel, path, fixed_size, cached_stamp,
                    self._album_root, lib_root,
                    is_image, is_video, still_image_time, duration,
                    generation or self._generation
                )
                self._schedule_job(base_key, job, priority)
            return retval

        # No cache entry - need to schedule job
        if base_key in self._pending_keys:
            # Already pending - update priority if higher
            existing_priority = self._pending_priorities.get(base_key, priority)
            if priority < existing_priority:
                self._store_job_spec(
                    base_key,
                    rel,
                    path,
                    fixed_size,
                    None,
                    self._album_root,
                    lib_root,
                    is_image,
                    is_video,
                    still_image_time,
                    duration,
                )
                self._pending_priorities[base_key] = priority
                job = self._create_job_from_spec(
                    rel, path, fixed_size, None,
                    self._album_root, lib_root,
                    is_image, is_video, still_image_time, duration,
                    generation or self._generation
                )
                self._schedule_job(base_key, job, priority)
            return None

        if base_key in self._failures:
            return None

        # Create new job with current generation
        current_gen = generation if generation is not None else self._generation
        self._store_job_spec(
            base_key,
            rel,
            path,
            fixed_size,
            None,
            self._album_root,
            lib_root,
            is_image,
            is_video,
            still_image_time,
            duration,
        )
        job = self._create_job_from_spec(
            rel, path, fixed_size, None,
            self._album_root, lib_root,
            is_image, is_video, still_image_time, duration,
            current_gen
        )

        self._schedule_job(base_key, job, priority)
        return None

    def _schedule_job(
        self,
        key: Tuple[str, str, int, int],
        job: ThumbnailJob,
        priority: "ThumbnailLoader.Priority"
    ) -> None:
        """Schedule a job in the priority queue."""
        self._sequence_counter += 1
        heapq.heappush(self._pending_heap, (priority, self._sequence_counter, key, job))
        self._pending_keys.add(key)
        self._pending_priorities[key] = priority
        self._drain_queue()

    def _drain_queue(self) -> None:
        """Process pending jobs by priority."""
        while self._active_jobs_count < self._max_active_jobs and self._pending_heap:
            # Pop the lowest priority (highest urgency) item
            priority, seq, key, job = heapq.heappop(self._pending_heap)

            # Check if this key is still in pending (may have been cancelled or replaced)
            if key not in self._pending_keys:
                continue

            # Validation jobs must still run even if a pixmap is hot in RAM.
            if key in self._failures or key in self._missing:
                self._pending_keys.discard(key)
                self._pending_priorities.pop(key, None)
                self._job_specs.pop(key, None)
                continue

            if key in self._memory and getattr(job, "_known_stamp", None) is None:
                self._pending_keys.discard(key)
                self._pending_priorities.pop(key, None)
                self._job_specs.pop(key, None)
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
        generation: int,
    ) -> ThumbnailJob:
        return ThumbnailJob(
            loader=self,
            rel=rel,
            abs_path=abs_path,
            size=size,
            known_stamp=known_stamp,
            album_root=album_root,
            library_root=library_root,
            is_image=is_image,
            is_video=is_video,
            still_image_time=still_image_time,
            duration=duration,
            generation=generation,
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
        self._pending_priorities.pop(base_key, None)

        if image is None:
            if self._retry_after_failure(base_key, rel):
                self._drain_queue()
                return
            already_retried = self._failure_counts.get(base_key, 0) >= 1
            self._record_terminal_failure(base_key, increment=not already_retried)
            self._drain_queue()
            return

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            if self._retry_after_failure(base_key, rel):
                self._drain_queue()
                return
            already_retried = self._failure_counts.get(base_key, 0) >= 1
            self._record_terminal_failure(base_key, increment=not already_retried)
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
        """Called when a job completes with valid cache (no decoding needed)."""
        base_key = full_key[:-1]
        self._active_jobs_count = max(0, self._active_jobs_count - 1)
        self._pending_keys.discard(base_key)
        self._pending_priorities.pop(base_key, None)
        self._drain_queue()

    def invalidate(self, rel: str) -> None:
        """Invalidate all cached thumbnails for the given rel path."""
        if self._album_root is None:
            return

        # Remove from memory cache
        to_remove = [k for k in self._memory if k[1] == rel]
        for k in to_remove:
            entry = self._memory.pop(k, None)
            if entry:
                stamp, pixmap = entry
                del pixmap

        # Clear pending
        self._pending_keys = {k for k in self._pending_keys if k[1] != rel}
        self._pending_priorities = {k: v for k, v in self._pending_priorities.items() if k[1] != rel}
        # Rebuild heap without invalidated items
        self._pending_heap = [
            item for item in self._pending_heap
            if item[2][1] != rel
        ]
        heapq.heapify(self._pending_heap)

        self._failures = {k for k in self._failures if k[1] != rel}
        self._missing = {k for k in self._missing if k[1] != rel}
        self._failure_counts = {k: v for k, v in self._failure_counts.items() if k[1] != rel}
        self._job_specs = {k: v for k, v in self._job_specs.items() if k[1] != rel}

    def _retry_after_failure(
        self,
        base_key: Tuple[str, str, int, int],
        rel: str,
    ) -> bool:
        """Attempt to retry a failed thumbnail job."""
        attempts = self._failure_counts.get(base_key, 0)
        if attempts >= 1:
            return False

        self._failure_counts[base_key] = attempts + 1

        spec = self._job_specs.get(base_key)
        if spec is None:
            return False

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

        job = self._create_job_from_spec(
            retry_rel, abs_path, size, known_stamp,
            album_root, library_root,
            is_image, is_video, still_image_time, duration,
            self._generation
        )

        self._schedule_job(base_key, job, self.Priority.NORMAL)
        return True

    @property
    def generation(self) -> int:
        """Return the current generation counter."""
        return self._generation

    @property
    def pending_count(self) -> int:
        """Return the number of pending jobs."""
        return len(self._pending_keys)
