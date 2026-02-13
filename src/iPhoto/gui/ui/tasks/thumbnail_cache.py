"""Thumbnail disk-cache utilities: path generation, validation, and IO."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from ....config import WORK_DIR_NAME


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


def write_cache(canvas: QImage, path: Path) -> bool:  # pragma: no cover - worker helper
    """Write a thumbnail *canvas* to *path* atomically via a temp file."""
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
