"""Shared values and bounded caches for the Detail opening pipeline."""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Literal

from PySide6.QtGui import QImage

from iPhoto.infrastructure.services.thumbnail_runtime_policy import (
    resolve_physical_memory_bytes,
)
from iPhoto.io.sidecar import sidecar_path_for_asset

_MIB = 1024 * 1024
DETAIL_DECODE_LEVELS = (1024, 2048, 3072, 4096)


@dataclass(frozen=True, slots=True)
class AssetSourceIdentity:
    """Indexed identity for neutral source pixels without GUI-thread stat I/O."""

    path: Path
    size_bytes: int = 0
    source_mtime_ns: int = 0
    index_revision: int = 0
    width: int = 0
    height: int = 0
    orientation: int = 1

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        size_bytes: object = 0,
        source_mtime_ns: object = 0,
        index_revision: object = 0,
        width: object = 0,
        height: object = 0,
        orientation: object = 1,
    ) -> AssetSourceIdentity:
        return cls(
            path=Path(path).expanduser().absolute(),
            size_bytes=_non_negative_int(size_bytes),
            source_mtime_ns=_non_negative_int(source_mtime_ns),
            index_revision=_non_negative_int(index_revision),
            width=_non_negative_int(width),
            height=_non_negative_int(height),
            orientation=_orientation_value(orientation),
        )

    @classmethod
    def from_info(cls, path: Path, info: Mapping[str, Any] | None) -> AssetSourceIdentity:
        values = info or {}
        return cls.create(
            path,
            size_bytes=values.get("bytes", values.get("size_bytes", 0)),
            source_mtime_ns=values.get("source_mtime_ns", 0),
            index_revision=values.get("index_revision", 0),
            width=values.get("w", values.get("width", 0)),
            height=values.get("h", values.get("height", 0)),
            orientation=values.get("orientation", values.get("image_orientation", 1)),
        )

    @property
    def revision(self) -> tuple[str, int, int]:
        if self.source_mtime_ns > 0:
            return ("mtime", self.size_bytes, self.source_mtime_ns)
        if self.index_revision > 0:
            return ("index", self.size_bytes, self.index_revision)
        return ("legacy", self.size_bytes, 0)


@dataclass(frozen=True, slots=True)
class DetailGeometryState:
    crop_cx: float = 0.5
    crop_cy: float = 0.5
    crop_width: float = 1.0
    crop_height: float = 1.0
    rotate90: int = 0
    straighten: float = 0.0
    perspective_vertical: float = 0.0
    perspective_horizontal: float = 0.0

    @classmethod
    def from_adjustments(cls, values: Mapping[str, Any] | None) -> DetailGeometryState:
        source = values or {}
        return cls(
            crop_cx=_clamp_float(source.get("Crop_CX", 0.5), 0.0, 1.0, 0.5),
            crop_cy=_clamp_float(source.get("Crop_CY", 0.5), 0.0, 1.0, 0.5),
            crop_width=_clamp_float(source.get("Crop_W", 1.0), 0.0001, 1.0, 1.0),
            crop_height=_clamp_float(source.get("Crop_H", 1.0), 0.0001, 1.0, 1.0),
            rotate90=_non_negative_int(source.get("Crop_Rotate90", 0)) % 4,
            straighten=_clamp_float(source.get("Crop_Straighten", 0.0), -45.0, 45.0, 0.0),
            perspective_vertical=_clamp_float(
                source.get("Perspective_Vertical", 0.0), -1.0, 1.0, 0.0
            ),
            perspective_horizontal=_clamp_float(
                source.get("Perspective_Horizontal", 0.0), -1.0, 1.0, 0.0
            ),
        )


DetailRequestReason = Literal["prefetch", "initial", "resize", "zoom"]
DetailResidencySlot = Literal["previous", "next"]
DetailDecodeLevel = int | Literal["full"]


@dataclass(frozen=True, slots=True)
class DetailRenderRequest:
    generation: int
    asset_id: str
    source_identity: AssetSourceIdentity
    viewport_physical_size: tuple[int, int]
    device_pixel_ratio: float
    geometry: DetailGeometryState
    reason: DetailRequestReason
    texture_limit: int = 8192
    raw_adjustments: Mapping[str, Any] | None = None
    decode_level: DetailDecodeLevel | None = None
    zoom_factor: float = 1.0
    residency_slot: DetailResidencySlot | None = None
    window_generation: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "raw_adjustments",
            MappingProxyType(dict(self.raw_adjustments or {})),
        )
        object.__setattr__(self, "zoom_factor", max(1.0, _finite_float(self.zoom_factor, 1.0)))
        object.__setattr__(self, "window_generation", _non_negative_int(self.window_generation))

    def with_decode_level(self) -> DetailRenderRequest:
        if self.decode_level is not None:
            return self
        return DetailRenderRequest(
            generation=self.generation,
            asset_id=self.asset_id,
            source_identity=self.source_identity,
            viewport_physical_size=self.viewport_physical_size,
            device_pixel_ratio=self.device_pixel_ratio,
            geometry=self.geometry,
            reason=self.reason,
            texture_limit=self.texture_limit,
            raw_adjustments=dict(self.raw_adjustments or {}),
            decode_level=select_detail_decode_level(self),
            zoom_factor=self.zoom_factor,
            residency_slot=self.residency_slot,
            window_generation=max(0, int(self.window_generation)),
        )


@dataclass(frozen=True, slots=True)
class DetailDecodeKey:
    asset_id: str
    source: Path
    source_revision: tuple[str, int, int]
    orientation: int
    decode_level: DetailDecodeLevel

    @classmethod
    def from_request(cls, request: DetailRenderRequest) -> DetailDecodeKey:
        prepared = request.with_decode_level()
        decode_level = prepared.decode_level or "full"
        identity = prepared.source_identity
        return cls(
            asset_id=str(prepared.asset_id).strip() or identity.path.name,
            source=identity.path,
            source_revision=identity.revision,
            orientation=identity.orientation,
            decode_level=decode_level,
        )


def select_detail_decode_level(request: DetailRenderRequest) -> DetailDecodeLevel:
    """Choose the smallest neutral-surface tier satisfying the visible viewport."""

    identity = request.source_identity
    if identity.width <= 0 or identity.height <= 0:
        return "full"
    source_w = max(1, identity.width)
    source_h = max(1, identity.height)
    viewport_w = max(1, int(request.viewport_physical_size[0]))
    viewport_h = max(1, int(request.viewport_physical_size[1]))
    geometry = request.geometry

    crop_pixel_w = max(1.0, source_w * geometry.crop_width)
    crop_pixel_h = max(1.0, source_h * geometry.crop_height)
    if geometry.rotate90 % 2:
        visible_w, visible_h = crop_pixel_h, crop_pixel_w
    else:
        visible_w, visible_h = crop_pixel_w, crop_pixel_h
    fit_scale = min(viewport_w / visible_w, viewport_h / visible_h)

    angle = math.radians(abs(geometry.straighten))
    straighten_scale = max(1.0, abs(math.cos(angle)) + abs(math.sin(angle)))
    perspective_scale = 1.0 + 0.5 * max(
        abs(geometry.perspective_vertical),
        abs(geometry.perspective_horizontal),
    )
    zoom_factor = max(1.0, _finite_float(request.zoom_factor, 1.0))
    required = math.ceil(
        max(source_w, source_h)
        * fit_scale
        * straighten_scale
        * perspective_scale
        * zoom_factor
    )
    source_longest = max(source_w, source_h)
    required = min(source_longest, max(1, required))
    effective_limit = max(1, min(source_longest, int(request.texture_limit or 8192)))
    if required > DETAIL_DECODE_LEVELS[-1]:
        return "full"
    for level in DETAIL_DECODE_LEVELS:
        if required <= level:
            return min(level, effective_limit, source_longest)
    return "full"


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
    source_identity: AssetSourceIdentity | None = None


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


def detail_scheduler_v3_enabled() -> bool:
    """Return whether same-source Detail decoder reuse is enabled."""

    value = os.environ.get("IPHOTO_DETAIL_SCHEDULER_V3", "1").strip().lower()
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


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _orientation_value(value: object) -> int:
    numeric = _non_negative_int(value)
    return numeric if 1 <= numeric <= 8 else 1


def _clamp_float(value: object, minimum: float, maximum: float, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(numeric):
        return default
    return max(minimum, min(maximum, numeric))


def _finite_float(value: object, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return numeric if math.isfinite(numeric) else default


__all__ = [
    "DETAIL_DECODE_LEVELS",
    "AssetSourceIdentity",
    "DetailDecodeKey",
    "DetailDecodeLevel",
    "DetailFrameCache",
    "DetailFrameIdentity",
    "DetailGeometryState",
    "DetailMediaPreparation",
    "DetailOpenTrace",
    "DetailPrefetchDescriptor",
    "DetailRenderRequest",
    "DetailResidencySlot",
    "VideoPresentationState",
    "detail_pipeline_v2_enabled",
    "detail_scheduler_v3_enabled",
    "select_detail_decode_level",
]
