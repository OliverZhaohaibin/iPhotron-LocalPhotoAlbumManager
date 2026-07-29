"""Shared values and bounded caches for the Detail opening pipeline."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

DETAIL_DECODE_LEVELS = (1024, 2048, 3072, 4096)

DetailMediaKind = Literal["image", "video", "live_motion"]
DetailTransactionReason = Literal[
    "click",
    "prefetch",
    "resize",
    "zoom",
    "live_replay",
]


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

    @property
    def has_stable_revision(self) -> bool:
        """Whether this identity can safely participate in a reusable cache."""

        return self.source_mtime_ns > 0 or self.index_revision > 0

    def repair_revision_from_stat(self) -> AssetSourceIdentity:
        """Fill a missing revision on a preparation worker.

        Callers must not invoke this from the GUI thread.  A failed stat leaves
        the identity unstable so cache layers can bypass it safely.
        """

        if self.has_stable_revision:
            return self
        try:
            source_stat = self.path.stat()
        except OSError:
            return self
        mtime_ns = max(0, int(getattr(source_stat, "st_mtime_ns", 0) or 0))
        if mtime_ns <= 0:
            return self
        return replace(
            self,
            size_bytes=max(0, int(source_stat.st_size)),
            source_mtime_ns=mtime_ns,
        )


@dataclass(frozen=True, slots=True)
class PlaybackAsyncToken:
    """Complete delivery identity for library-scoped playback work."""

    library_epoch: int
    asset_generation: int
    asset_id: str
    source_path: Path
    source_revision: tuple[str, int, int]

    @classmethod
    def create(
        cls,
        *,
        library_epoch: int,
        asset_generation: int,
        asset_id: str,
        source_identity: AssetSourceIdentity,
    ) -> "PlaybackAsyncToken":
        return cls(
            library_epoch=max(0, int(library_epoch)),
            asset_generation=max(0, int(asset_generation)),
            asset_id=str(asset_id).strip() or source_identity.path.name,
            source_path=source_identity.path,
            source_revision=source_identity.revision,
        )

    def matches(
        self,
        *,
        library_epoch: int,
        asset_generation: int,
        asset_id: str,
        source_identity: AssetSourceIdentity,
    ) -> bool:
        return self == PlaybackAsyncToken.create(
            library_epoch=library_epoch,
            asset_generation=asset_generation,
            asset_id=asset_id,
            source_identity=source_identity,
        )


@dataclass(frozen=True, slots=True)
class DetailRenderTransaction:
    """Immutable identity shared by still and video Detail render work."""

    generation: int
    asset_id: str
    media_kind: DetailMediaKind
    source_identity: AssetSourceIdentity
    viewport_physical_size: tuple[int, int] = (0, 0)
    device_pixel_ratio: float = 1.0
    reason: DetailTransactionReason = "click"

    def __post_init__(self) -> None:
        object.__setattr__(self, "generation", _non_negative_int(self.generation))
        object.__setattr__(self, "asset_id", str(self.asset_id).strip())
        object.__setattr__(
            self,
            "viewport_physical_size",
            (
                _non_negative_int(self.viewport_physical_size[0]),
                _non_negative_int(self.viewport_physical_size[1]),
            ),
        )
        object.__setattr__(
            self,
            "device_pixel_ratio",
            max(0.1, _finite_float(self.device_pixel_ratio, 1.0)),
        )

    def with_viewport(
        self,
        viewport_physical_size: tuple[int, int],
        device_pixel_ratio: float,
    ) -> DetailRenderTransaction:
        """Return the same transaction identity with current render metrics."""

        return DetailRenderTransaction(
            generation=self.generation,
            asset_id=self.asset_id,
            media_kind=self.media_kind,
            source_identity=self.source_identity,
            viewport_physical_size=viewport_physical_size,
            device_pixel_ratio=device_pixel_ratio,
            reason=self.reason,
        )


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
            crop_cx=_finite_float(source.get("Crop_CX", 0.5), 0.5),
            crop_cy=_finite_float(source.get("Crop_CY", 0.5), 0.5),
            crop_width=max(0.0001, _finite_float(source.get("Crop_W", 1.0), 1.0)),
            crop_height=max(0.0001, _finite_float(source.get("Crop_H", 1.0), 1.0)),
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

    @classmethod
    def from_transaction(
        cls,
        transaction: DetailRenderTransaction,
        *,
        geometry: DetailGeometryState,
        reason: DetailRequestReason,
        texture_limit: int = 8192,
        raw_adjustments: Mapping[str, Any] | None = None,
        decode_level: DetailDecodeLevel | None = None,
        zoom_factor: float = 1.0,
        residency_slot: DetailResidencySlot | None = None,
        window_generation: int = 0,
    ) -> DetailRenderRequest:
        if transaction.media_kind == "video":
            raise ValueError("Video transactions cannot create still decode requests")
        return cls(
            generation=transaction.generation,
            asset_id=transaction.asset_id,
            source_identity=transaction.source_identity,
            viewport_physical_size=transaction.viewport_physical_size,
            device_pixel_ratio=transaction.device_pixel_ratio,
            geometry=geometry,
            reason=reason,
            texture_limit=texture_limit,
            raw_adjustments=raw_adjustments,
            decode_level=decode_level,
            zoom_factor=zoom_factor,
            residency_slot=residency_slot,
            window_generation=window_generation,
        )

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
    transaction: DetailRenderTransaction | None = None

    @property
    def generation(self) -> int:
        if self.transaction is not None:
            return self.transaction.generation
        return self.request_generation


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
    "DetailGeometryState",
    "DetailMediaPreparation",
    "DetailOpenTrace",
    "DetailPrefetchDescriptor",
    "PlaybackAsyncToken",
    "DetailRenderTransaction",
    "DetailRenderRequest",
    "DetailResidencySlot",
    "VideoPresentationState",
    "select_detail_decode_level",
]
