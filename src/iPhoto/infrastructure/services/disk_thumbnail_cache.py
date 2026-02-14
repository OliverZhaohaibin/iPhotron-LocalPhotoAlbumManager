"""L2: Disk-based thumbnail cache with MD5 hash bucketing."""

from __future__ import annotations

import hashlib
from pathlib import Path


class DiskThumbnailCache:
    """L2: Disk thumbnail cache using hash-bucketed directory layout."""

    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> bytes | None:
        path = self._key_to_path(key)
        if path.exists():
            return path.read_bytes()
        return None

    def put(self, key: str, data: bytes) -> None:
        path = self._key_to_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def invalidate(self, key: str) -> None:
        path = self._key_to_path(key)
        if path.exists():
            path.unlink(missing_ok=True)

    def _key_to_path(self, key: str) -> Path:
        # MD5 is used here solely for uniform hash distribution across
        # bucket directories — NOT for cryptographic security.
        hash_hex = hashlib.md5(key.encode()).hexdigest()  # noqa: S324
        return self._cache_dir / hash_hex[:2] / f"{hash_hex}.jpg"
