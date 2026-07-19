"""Shared thumbnail cache key helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

DEFAULT_THUMBNAIL_SIZE = (512, 512)


def thumbnail_cache_key(
    path: Path,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
) -> str:
    """Return the stable disk-cache key for a thumbnail request."""

    width, height = size
    payload = f"{Path(path).as_posix()}_{int(width)}x{int(height)}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()  # noqa: S324


def thumbnail_cache_file(
    cache_dir: Path,
    path: Path,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
) -> Path:
    """Return the disk-cache file for *path* and *size*."""

    return Path(cache_dir) / f"{thumbnail_cache_key(path, size)}.jpg"


def thumbnail_cache_file_for_key(cache_dir: Path, key: str) -> Path:
    """Return the disk-cache file for a previously computed cache key."""

    return Path(cache_dir) / f"{key}.jpg"
