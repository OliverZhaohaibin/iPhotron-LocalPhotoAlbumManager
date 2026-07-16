"""Shared values and bounded caches for the Detail opening pipeline."""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from PySide6.QtGui import QImage

from iPhoto.infrastructure.services.thumbnail_runtime_policy import (
    resolve_physical_memory_bytes,
)
from iPhoto.io.sidecar import sidecar_path_for_asset

_MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DetailFrameIdentity:
    """Versioned identity for one fully decoded still frame."""

    path: Path
    size: int
    mtime_ns: int
    sidecar_size: int
    sidecar_mtime_ns: int
    quality: Literal["full"] = "full"

    @classmethod
    def from_path(cls, path: Path) -> DetailFrameIdentity:
        normalized = _normalized(path)
        size, mtime_ns = _stat_identity(normalized)
        sidecar = sidecar_path_for_asset(normalized)
        sidecar_size, sidecar_mtime_ns = _stat_identity(sidecar)
        return cls(
            path=normalized,
            size=size,
            mtime_ns=mtime_ns,
            sidecar_size=sidecar_size,
            sidecar_mtime_ns=sidecar_mtime_ns,
        )


@dataclass(frozen=True, slots=True)
class DetailMediaPreparation:
    request_generation: int
    path: Path
    adjustments: dict[str, Any]
    trim_range_ms: tuple[int, int] | None = None
    adjusted_preview: bool = False
    rotation_cw: int = 0
    raw_width: int = 0
    raw_height: int = 0
    linux_180_hint: bool = False


@dataclass(frozen=True, slots=True)
class VideoPresentationState:
    request_generation: int
    adjustments: dict[str, Any]
    trim_range_ms: tuple[int, int] | None
    adjusted_preview: bool
    rotation_cw: int
    raw_width: int
    raw_height: int
    linux_180_hint: bool


@dataclass(slots=True)
class DetailOpenTrace:
    request_generation: int
    row: int
    asset_id: str = ""
    media_kind: str = "unknown"


@dataclass(frozen=True, slots=True)
class DetailPrefetchDescriptor:
    row: int
    asset_id: str
    path: Path
    is_video: bool


class DetailFrameCache:
    """Thread-safe byte-budgeted LRU for full-resolution QImages."""

    def __init__(self, budget_bytes: int | None = None, *, max_entries: int = 3) -> None:
        physical = resolve_physical_memory_bytes()
        derived = max(64 * _MIB, min(256 * _MIB, int(physical * 0.01)))
        self._budget_bytes = max(_MIB, int(budget_bytes or derived))
        self._max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[
            DetailFrameIdentity, tuple[QImage, dict[str, Any], int]
        ] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    @property
    def budget_bytes(self) -> int:
        return self._budget_bytes

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return self._bytes

    def get(
        self,
        identity: DetailFrameIdentity,
    ) -> tuple[QImage, dict[str, Any]] | None:
        with self._lock:
            cached = self._entries.pop(identity, None)
            if cached is None:
                return None
            self._entries[identity] = cached
            image, adjustments, _image_bytes = cached
            return QImage(image), dict(adjustments)

    def put(
        self,
        identity: DetailFrameIdentity,
        image: QImage,
        adjustments: dict[str, Any],
    ) -> bool:
        if image.isNull():
            return False
        image_bytes = _image_bytes(image)
        # Oversized current frames remain owned by the viewer but are not kept
        # in this revisit cache.
        if image_bytes > self._budget_bytes:
            return False
        with self._lock:
            previous = self._entries.pop(identity, None)
            if previous is not None:
                self._bytes -= previous[2]
            self._entries[identity] = (QImage(image), dict(adjustments), image_bytes)
            self._bytes += image_bytes
            self._trim_locked()
        return True

    def invalidate_path(self, path: Path) -> None:
        normalized = _normalized(path)
        with self._lock:
            for identity in tuple(self._entries):
                if identity.path == normalized:
                    _image, _adjustments, image_bytes = self._entries.pop(identity)
                    self._bytes -= image_bytes

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def _trim_locked(self) -> None:
        while (
            len(self._entries) > self._max_entries
            or self._bytes > self._budget_bytes
        ):
            _identity, (_image, _adjustments, image_bytes) = self._entries.popitem(last=False)
            self._bytes -= image_bytes


def detail_pipeline_v2_enabled() -> bool:
    value = os.environ.get("IPHOTO_DETAIL_PIPELINE_V2", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _normalized(path: Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser()


def _stat_identity(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
    return (int(stat.st_size), int(mtime_ns))


def _image_bytes(image: QImage) -> int:
    size_in_bytes = getattr(image, "sizeInBytes", None)
    if callable(size_in_bytes):
        return max(0, int(size_in_bytes()))
    return max(0, int(image.bytesPerLine()) * int(image.height()))


__all__ = [
    "DetailFrameCache",
    "DetailFrameIdentity",
    "DetailMediaPreparation",
    "DetailOpenTrace",
    "DetailPrefetchDescriptor",
    "VideoPresentationState",
    "detail_pipeline_v2_enabled",
]
