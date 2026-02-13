"""ThumbnailJob QRunnable for background thumbnail rendering."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING

try:
    import psutil
except ImportError:
    psutil = None

from PySide6.QtCore import QRunnable, QSize
from PySide6.QtGui import QImage

import numpy as np

from ....io import sidecar
from .thumbnail_cache import safe_unlink, stat_mtime_ns, generate_cache_path, write_cache
from .thumbnail_renderer import render_image, render_video, seek_targets

if TYPE_CHECKING:
    from .thumbnail_loader import ThumbnailLoader


LOGGER = logging.getLogger(__name__)


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
                success = write_cache(image, cache_path)
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
            return render_video(
                self._abs_path,
                self._size,
                still_image_time=self._still_image_time,
                duration=self._duration,
            )
        if self._is_image:
            return render_image(self._abs_path, self._size)
        return None

    def _seek_targets(self) -> list[Optional[float]]:
        """Return seek offsets – delegates to :func:`thumbnail_renderer.seek_targets`."""
        return seek_targets(self._is_video, self._still_image_time, self._duration)
