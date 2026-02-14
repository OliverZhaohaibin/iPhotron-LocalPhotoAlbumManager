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

from collections import OrderedDict, deque
from enum import IntEnum
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QSize,
    QThreadPool,
    Qt,
    Signal,
)
from PySide6.QtGui import QImage, QPixmap

from ....config import WORK_DIR_NAME
from ....utils.pathutils import ensure_work_dir

# Re-exported names – keep every symbol that downstream code may import
# from ``iPhoto.gui.ui.tasks.thumbnail_loader``.
from .thumbnail_cache import safe_unlink, stat_mtime_ns, generate_cache_path  # noqa: F401
from .thumbnail_job import ThumbnailJob  # noqa: F401


LOGGER = logging.getLogger(__name__)


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
