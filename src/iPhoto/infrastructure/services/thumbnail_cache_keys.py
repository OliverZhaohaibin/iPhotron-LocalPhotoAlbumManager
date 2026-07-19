"""Shared thumbnail cache key helpers."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_THUMBNAIL_SIZE = (512, 512)
THUMBNAIL_RENDER_VERSION = "gallery-v2-edit-aware"


@dataclass(frozen=True, slots=True)
class ThumbnailFingerprint:
    """Immutable source/edit identity for one rendered thumbnail artifact."""

    source_mtime_ns: int
    source_size: int
    sidecar_digest: str
    cache_key: str


def _mtime_ns(stat_result: os.stat_result) -> int:
    value = getattr(stat_result, "st_mtime_ns", None)
    if value is None:
        value = int(stat_result.st_mtime * 1_000_000_000)
    return int(value)


def thumbnail_fingerprint(
    path: Path,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
) -> ThumbnailFingerprint:
    """Return a content-version identity including the ``.ipo`` sidecar."""

    source = Path(path)
    try:
        normalized = source.expanduser().resolve()
    except OSError:
        normalized = source.expanduser().absolute()
    try:
        source_stat = source.stat()
        source_mtime_ns = _mtime_ns(source_stat)
        source_size = int(source_stat.st_size)
    except OSError:
        source_mtime_ns = 0
        source_size = 0

    sidecar_path = source.with_suffix(".ipo")
    try:
        sidecar_payload = sidecar_path.read_bytes()
    except OSError:
        sidecar_payload = b""
    sidecar_digest = hashlib.blake2b(sidecar_payload, digest_size=16).hexdigest()

    width, height = size
    payload = "\0".join(
        (
            THUMBNAIL_RENDER_VERSION,
            normalized.as_posix(),
            str(source_mtime_ns),
            str(source_size),
            sidecar_digest,
            f"{int(width)}x{int(height)}",
        )
    )
    cache_key = hashlib.blake2b(payload.encode("utf-8"), digest_size=20).hexdigest()
    return ThumbnailFingerprint(
        source_mtime_ns=source_mtime_ns,
        source_size=source_size,
        sidecar_digest=sidecar_digest,
        cache_key=cache_key,
    )


def thumbnail_cache_key(
    path: Path,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
) -> str:
    """Return the immutable source/edit-version key for a thumbnail request."""

    return thumbnail_fingerprint(path, size).cache_key


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
