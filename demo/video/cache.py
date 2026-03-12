"""Disk cache for video thumbnail strips.

Caches the generated thumbnail strip image per video so that repeated
opens of the same video are instant (< 10 ms).  The cache key is a
SHA-256 hash of ``(video_path, file_size, mtime, thumb_h, count)``.
"""

from __future__ import annotations

import hashlib
import os
import struct
import time

from config import CACHE_DIR


def _cache_key(video_path: str, thumb_h: int, count: int) -> str:
    """Compute a deterministic cache key for a video thumbnail strip."""
    try:
        st = os.stat(video_path)
        blob = f"{os.path.abspath(video_path)}\x00{st.st_size}\x00{st.st_mtime_ns}\x00{thumb_h}\x00{count}"
        return hashlib.sha256(blob.encode()).hexdigest()
    except OSError:
        return ""


def cache_get(video_path: str, thumb_h: int, count: int):
    """Retrieve cached thumbnail strip bytes.

    Returns ``(thumb_w, thumb_h, count, data_bytes)`` or *None*.
    The data is a raw RGB888 buffer of size ``thumb_w * thumb_h * 3 * count``.
    """
    key = _cache_key(video_path, thumb_h, count)
    if not key:
        return None
    path = os.path.join(CACHE_DIR, key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return None
            w, h, n = struct.unpack("<III", header)
            expected = w * h * 3 * n
            data = f.read(expected)
            if len(data) != expected:
                return None
            return w, h, n, data
    except OSError:
        return None


def cache_put(video_path: str, thumb_w: int, thumb_h: int, count: int,
              data: bytes) -> None:
    """Store thumbnail strip bytes to disk cache."""
    key = _cache_key(video_path, thumb_h, count)
    if not key:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, key)
        header = struct.pack("<III", thumb_w, thumb_h, count)
        with open(path, "wb") as f:
            f.write(header)
            f.write(data)
        # Prune old entries (keep cache < 200 MB)
        _prune_cache(max_bytes=200 * 1024 * 1024)
    except OSError as e:
        print(f"[cache] Write error: {e}")


def _prune_cache(max_bytes: int = 200 * 1024 * 1024) -> None:
    """Remove oldest cache entries when total size exceeds *max_bytes*."""
    try:
        entries = []
        total = 0
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            if os.path.isfile(p):
                st = os.stat(p)
                entries.append((st.st_mtime, st.st_size, p))
                total += st.st_size
        if total <= max_bytes:
            return
        entries.sort()  # oldest first
        for _mtime, sz, path in entries:
            if total <= max_bytes:
                break
            try:
                os.remove(path)
                total -= sz
            except OSError:
                pass
    except OSError:
        pass
