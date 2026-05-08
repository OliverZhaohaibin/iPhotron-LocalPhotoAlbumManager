"""Thumbnail disk-cache utilities: path generation, validation, and IO."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from ....utils.pathutils import ensure_work_dir


LOGGER = logging.getLogger(__name__)

# Current thumbnail pipeline version for cache invalidation
THUMB_PIPELINE_VERSION = "thumb-v1"


def build_thumb_key(
    library_id: str,
    asset_id: str | int,
    normalized_rel_path: str,
    file_size: int,
    mtime_ns: int,
    orientation: int = 0,
    edit_version: int = 0,
    pipeline_version: str = THUMB_PIPELINE_VERSION,
) -> str:
    """Generate a stable thumbnail key for cache lookups.

    The key is based on:
    - library_id: Library identity to avoid conflicts between libraries
    - asset_id: Asset identifier for uniqueness
    - normalized_rel_path: Normalized relative path
    - file_size: File size to detect copies
    - mtime_ns: Modification time in nanoseconds
    - orientation: Image orientation
    - edit_version: Edit sidecar version
    - pipeline_version: Thumbnail generation pipeline version

    This ensures:
    - Parent/child albums with same filename don't conflict
    - File moves within library are detected (if rel_path changes)
    - Edits to original file are detected
    - Thumbnail pipeline changes trigger regeneration

    Args:
        library_id: Library identity string
        asset_id: Asset identifier
        normalized_rel_path: Normalized relative path (POSIX style)
        file_size: File size in bytes
        mtime_ns: Modification time in nanoseconds
        orientation: Image orientation (0-8)
        edit_version: Edit sidecar version
        pipeline_version: Pipeline version string

    Returns:
        SHA1 hex digest of the composite key
    """
    raw = (
        f"{library_id}|{asset_id}|{normalized_rel_path}|"
        f"{file_size}|{mtime_ns}|{orientation}|"
        f"{edit_version}|{pipeline_version}"
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


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
            pass
    except OSError:
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
    return ensure_work_dir(library_root) / "thumbs" / filename


def generate_cache_path_from_thumb_key(
    library_root: Path,
    thumb_key: str,
    size: QSize,
) -> Path:
    """
    Generate the file path for a cached thumbnail using the thumb key.

    Uses hash-based directory sharding for better filesystem performance
    with large numbers of cached thumbnails.

    Args:
        library_root: The library root directory
        thumb_key: The stable thumbnail key
        size: The thumbnail size

    Returns:
        Path: The path to the cache file
    """
    # Create sharded path: thumbs/<first 2 chars>/<next 2 chars>/<thumb_key>.png
    subdir1 = thumb_key[:2]
    subdir2 = thumb_key[2:4]
    filename = f"{thumb_key}_{size.width()}x{size.height()}.png"
    thumb_dir = ensure_work_dir(library_root) / "thumbs" / subdir1 / subdir2
    return thumb_dir / filename


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
        else:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        pass
    return False
