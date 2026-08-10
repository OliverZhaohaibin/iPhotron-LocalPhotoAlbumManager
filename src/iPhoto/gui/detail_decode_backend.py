"""Viewport-aware neutral still-image decode backends for Detail."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QColorSpace, QImage, QImageReader

from iPhoto.core.color_resolver import ColorStats
from iPhoto.core.raw_processor import is_raw_extension
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailDecodeLevel,
    DetailRenderRequest,
)
from iPhoto.gui.detail_profile import emit_detail_event
from iPhoto.gui.detail_surface_residency import (
    SurfaceByteBreakdown,
    SurfaceResidencyTracker,
)
from iPhoto.utils.deps import load_pillow

_MAX_DETAIL_SURFACE_BYTES = 192 * 1024 * 1024


class DecodeCancelledError(RuntimeError):
    """Raised at a cooperative checkpoint after a request becomes stale."""


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...


class StillDecodeBackend(Protocol):
    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface: ...


class StillDecodeBackendRegistry:
    """Select native-capable platform decoders without coupling the scheduler."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        macos_backend: StillDecodeBackend | None = None,
        windows_backend: StillDecodeBackend | None = None,
        qt_backend: StillDecodeBackend | None = None,
        raw_backend: StillDecodeBackend | None = None,
        residency_tracker: SurfaceResidencyTracker | None = None,
    ) -> None:
        self._platform = sys.platform if platform is None else platform
        self._qt = qt_backend or QtStillDecodeBackend()
        self._raw = raw_backend or RawStillDecodeBackend(
            residency_tracker=residency_tracker
        )
        self._macos = macos_backend or (
            _load_macos_imageio_backend() if self._platform == "darwin" else None
        )
        self._windows = windows_backend or (
            _load_windows_wic_backend() if self._platform == "win32" else None
        )

    def backend_for(self, request: DetailRenderRequest) -> StillDecodeBackend:
        if is_raw_extension(request.source_identity.path.suffix):
            return self._raw
        if self._platform == "darwin" and self._macos is not None:
            return FallbackStillDecodeBackend(
                self._macos,
                self._qt,
                fallback_name="imageio_to_qt",
            )
        if self._platform == "win32" and self._windows is not None:
            return FallbackStillDecodeBackend(
                self._windows,
                self._qt,
                fallback_name="wic_to_qt",
            )
        return self._qt


class FallbackStillDecodeBackend:
    """Try one platform backend and fall back to Qt inside the worker lane."""

    def __init__(
        self,
        primary: StillDecodeBackend,
        fallback: StillDecodeBackend,
        *,
        fallback_name: str,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_name = fallback_name

    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface:
        try:
            return self._primary.decode(request, cancellation)
        except DecodeCancelledError:
            raise
        except Exception:  # noqa: BLE001 - platform codecs have backend-specific failures
            _check_cancelled(cancellation)
            surface = self._fallback.decode(request, cancellation)
            return DecodedSurface(
                image=surface.image,
                decode_key=surface.decode_key,
                source_size=surface.source_size,
                decoded_size=surface.decoded_size,
                decode_level=surface.decode_level,
                backend=surface.backend,
                color_stats=surface.color_stats,
                fallback=self._fallback_name,
                pixel_format=surface.pixel_format,
                color_space=surface.color_space,
                orientation_applied=surface.orientation_applied,
                cache_tier=surface.cache_tier,
                backing_owner=surface.backing_owner,
            )


@dataclass(frozen=True, slots=True)
class DecodedSurface:
    """Detached neutral upload surface owned by one scheduler delivery."""

    image: QImage
    decode_key: DetailDecodeKey
    source_size: tuple[int, int]
    decoded_size: tuple[int, int]
    decode_level: DetailDecodeLevel
    backend: str
    color_stats: ColorStats = ColorStats()
    fallback: str | None = None
    pixel_format: str = "rgba8888"
    color_space: str = "srgb"
    orientation_applied: bool = True
    cache_tier: Literal["decode", "memory", "disk"] = "decode"
    backing_owner: object | None = None


def _check_cancelled(token: CancellationToken) -> None:
    if token.is_cancelled():
        raise DecodeCancelledError("Still-image decode cancelled")


def _load_macos_imageio_backend() -> StillDecodeBackend | None:
    try:
        from iPhoto.gui.detail_decode_macos import create_macos_imageio_backend
    except ImportError:
        return None
    return create_macos_imageio_backend()


def _load_windows_wic_backend() -> StillDecodeBackend | None:
    try:
        from iPhoto.gui.detail_decode_windows import create_windows_wic_backend
    except ImportError:
        return None
    return create_windows_wic_backend()


def _target_longest_edge(request: DetailRenderRequest) -> int:
    prepared = request.with_decode_level()
    identity = prepared.source_identity
    source_longest = max(1, identity.width, identity.height)
    if prepared.decode_level == "full":
        if identity.width <= 0 or identity.height <= 0:
            return max(1, prepared.texture_limit)
        return min(source_longest, max(1, prepared.texture_limit))
    return min(int(prepared.decode_level or source_longest), max(1, prepared.texture_limit))


def _target_size(request: DetailRenderRequest) -> QSize:
    identity = request.source_identity
    width = max(1, identity.width)
    height = max(1, identity.height)
    longest = _target_longest_edge(request)
    if width >= height:
        target = QSize(longest, max(1, round(longest * height / width)))
    else:
        target = QSize(max(1, round(longest * width / height)), longest)
    estimated_bytes = target.width() * target.height() * 4
    if estimated_bytes > _MAX_DETAIL_SURFACE_BYTES:
        scale = (_MAX_DETAIL_SURFACE_BYTES / float(estimated_bytes)) ** 0.5
        target = QSize(
            max(1, int(target.width() * scale)),
            max(1, int(target.height() * scale)),
        )
    return target


def _normalise_surface(image: QImage, target: QSize) -> QImage:
    if image.isNull():
        return QImage()
    if target.isValid() and not target.isEmpty() and (
        image.width() > target.width() or image.height() > target.height()
    ):
        image = image.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    srgb = QColorSpace(QColorSpace.NamedColorSpace.SRgb)
    try:
        if image.colorSpace().isValid() and image.colorSpace() != srgb:
            image = image.convertedToColorSpace(srgb)
    except (AttributeError, RuntimeError, TypeError):
        pass
    normalised = image.convertToFormat(QImage.Format.Format_RGBA8888).copy()
    if not normalised.colorSpace().isValid():
        normalised.setColorSpace(srgb)
    return normalised


class QtStillDecodeBackend:
    """Use QImageReader scaled decode, with Pillow as a compatibility fallback."""

    name = "qt"

    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface:
        prepared = request.with_decode_level()
        source = prepared.source_identity.path
        target = _target_size(prepared)
        _check_cancelled(cancellation)
        reader = QImageReader(str(source))
        disable_cache = getattr(reader, "setCacheEnabled", None)
        if callable(disable_cache):
            disable_cache(False)
        reader.setAutoTransform(True)
        intrinsic = reader.size()
        if intrinsic.isValid() and not intrinsic.isEmpty():
            reader_target = target
            if prepared.source_identity.orientation in (5, 6, 7, 8):
                reader_target = QSize(target.height(), target.width())
            scaled = intrinsic.scaled(reader_target, Qt.AspectRatioMode.KeepAspectRatio)
            if scaled.width() < intrinsic.width() or scaled.height() < intrinsic.height():
                reader.setScaledSize(scaled)
        image = reader.read()
        _check_cancelled(cancellation)
        fallback = None
        if image.isNull():
            image = _load_with_pillow(source, target)
            fallback = "pillow"
        elif image.width() > target.width() or image.height() > target.height():
            fallback = "qt_full_scale"
        if image.isNull():
            raise RuntimeError(reader.errorString() or "Image decoder returned an empty frame")
        surface = _normalise_surface(image, target)
        _check_cancelled(cancellation)
        if surface.isNull():
            raise RuntimeError("Image decoder returned an empty neutral surface")
        source_size = (
            max(1, prepared.source_identity.width or intrinsic.width() or surface.width()),
            max(1, prepared.source_identity.height or intrinsic.height() or surface.height()),
        )
        return DecodedSurface(
            image=surface,
            decode_key=DetailDecodeKey.from_request(prepared),
            source_size=source_size,
            decoded_size=(surface.width(), surface.height()),
            decode_level=prepared.decode_level or "full",
            backend=self.name,
            fallback=fallback,
        )


class RawStillDecodeBackend:
    """Prefer an adequate embedded RAW preview, then half/full demosaic."""

    name = "rawpy"

    def __init__(
        self,
        *,
        residency_tracker: SurfaceResidencyTracker | None = None,
    ) -> None:
        self._residency_tracker = residency_tracker

    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface:
        prepared = request.with_decode_level()
        target = _target_size(prepared)
        _check_cancelled(cancellation)
        rawpy = _import_rawpy()
        if rawpy is None:
            raise RuntimeError("rawpy is unavailable")
        source = prepared.source_identity.path
        fallback: str | None = None
        image = QImage()
        preview_size = QSize()
        half_size = QSize()
        with rawpy.imread(str(source)) as raw:
            _check_cancelled(cancellation)
            raw_size = _raw_visible_size(raw, prepared.source_identity)
            half_size = QSize(
                max(1, (raw_size.width() + 1) // 2),
                max(1, (raw_size.height() + 1) // 2),
            )
            thumb = None
            try:
                thumb = raw.extract_thumb()
            except Exception:  # noqa: BLE001 - embedded previews are optional codec data
                thumb = None
            preview_size = _raw_thumb_size(thumb, rawpy)
            if _size_satisfies(preview_size, target):
                candidate = "embedded"
            elif _size_satisfies(half_size, target):
                candidate = "half"
                fallback = "half"
            else:
                candidate = "full"
                fallback = "full"
            if candidate == "embedded":
                started = time.perf_counter()
                image = _qimage_from_raw_thumb(
                    thumb,
                    rawpy,
                    target,
                    orientation=prepared.source_identity.orientation,
                )
                emit_detail_event(
                    "raw_thumb_decode",
                    generation=prepared.generation,
                    asset_id=prepared.asset_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    width=image.width(),
                    height=image.height(),
                )
                _check_cancelled(cancellation)
                if image.isNull():
                    candidate = "half" if _size_satisfies(half_size, target) else "full"
                    fallback = candidate
            emit_detail_event(
                "raw_candidate_selected",
                generation=prepared.generation,
                asset_id=prepared.asset_id,
                suffix=source.suffix.lower(),
                candidate=candidate,
                decode_level=prepared.decode_level or "full",
                target_width=target.width(),
                target_height=target.height(),
                preview_width=preview_size.width(),
                preview_height=preview_size.height(),
                half_width=half_size.width(),
                half_height=half_size.height(),
            )
            if candidate != "embedded":
                started = time.perf_counter()
                rgb = _postprocess_raw(raw, half_size=candidate == "half")
                emit_detail_event(
                    "raw_postprocess",
                    generation=prepared.generation,
                    asset_id=prepared.asset_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    candidate=candidate,
                )
                _check_cancelled(cancellation)
                started = time.perf_counter()
                raw_resource_id = (
                    "raw-intermediate",
                    prepared.asset_id,
                    prepared.generation,
                    id(rgb),
                )
                raw_owner_id = f"raw-decoder:{prepared.generation}:{prepared.asset_id}"
                if self._residency_tracker is not None:
                    self._residency_tracker.retain(
                        raw_owner_id,
                        "raw_decoder",
                        raw_resource_id,
                        SurfaceByteBreakdown(
                            raw_intermediate=max(0, int(getattr(rgb, "nbytes", 0)))
                        ),
                        generation=prepared.generation,
                    )
                try:
                    image = _qimage_from_array(rgb)
                finally:
                    if self._residency_tracker is not None:
                        self._residency_tracker.release(
                            raw_owner_id,
                            raw_resource_id,
                            generation=prepared.generation,
                        )
                emit_detail_event(
                    "raw_surface_convert",
                    generation=prepared.generation,
                    asset_id=prepared.asset_id,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    candidate=candidate,
                    phase="array_bridge",
                    width=image.width(),
                    height=image.height(),
                )
            _check_cancelled(cancellation)
        _check_cancelled(cancellation)
        if image.isNull():
            raise RuntimeError("RAW decoder returned an empty frame")
        started = time.perf_counter()
        surface = _normalise_surface(image, target)
        emit_detail_event(
            "raw_surface_convert",
            generation=prepared.generation,
            asset_id=prepared.asset_id,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            candidate=candidate,
            phase="normalise",
            width=surface.width(),
            height=surface.height(),
        )
        _check_cancelled(cancellation)
        return DecodedSurface(
            image=surface,
            decode_key=DetailDecodeKey.from_request(prepared),
            source_size=(
                max(1, prepared.source_identity.width or image.width()),
                max(1, prepared.source_identity.height or image.height()),
            ),
            decoded_size=(surface.width(), surface.height()),
            decode_level=prepared.decode_level or "full",
            backend=self.name,
            fallback=fallback,
        )


class DefaultStillDecodeBackend:
    """Route RAW formats to rawpy and all other stills to Qt."""

    def __init__(
        self,
        registry: StillDecodeBackendRegistry | None = None,
        *,
        residency_tracker: SurfaceResidencyTracker | None = None,
    ) -> None:
        self._registry = registry or StillDecodeBackendRegistry(
            residency_tracker=residency_tracker
        )

    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface:
        return self._registry.backend_for(request).decode(request, cancellation)


def _load_with_pillow(source: Path, target: QSize) -> QImage:
    pillow = load_pillow()
    if pillow is None:
        return QImage()
    try:
        with pillow.Image.open(source) as opened:
            image = pillow.ImageOps.exif_transpose(opened)
            resampling = getattr(pillow.Image, "Resampling", pillow.Image)
            image.thumbnail(
                (target.width(), target.height()),
                getattr(resampling, "LANCZOS", pillow.Image.BICUBIC),
            )
            return QImage(pillow.ImageQt(image.convert("RGBA"))).copy()
    except Exception:  # noqa: BLE001 - Pillow plugins raise codec-specific exceptions
        return QImage()


def _import_rawpy() -> Any | None:
    try:
        import rawpy  # type: ignore[import-untyped]
    except ImportError:
        return None
    return rawpy


def probe_raw_source_identity(identity: AssetSourceIdentity) -> AssetSourceIdentity:
    """Resolve missing RAW geometry from LibRaw without filesystem metadata I/O."""

    if identity.width > 0 and identity.height > 0:
        return identity
    rawpy = _import_rawpy()
    if rawpy is None:
        raise RuntimeError("rawpy is unavailable")
    try:
        with rawpy.imread(str(identity.path)) as raw:
            size = _raw_visible_size(raw, identity)
            flip = _raw_flip(raw)
    except Exception as exc:  # noqa: BLE001 - LibRaw exposes format-specific failures
        raise RuntimeError(f"Unable to probe RAW geometry: {exc}") from exc
    if size.isEmpty() or not size.isValid():
        raise RuntimeError("RAW decoder did not report valid source geometry")
    orientation = identity.orientation
    if flip in (5, 6):
        orientation = 8 if flip == 5 else 6
    elif flip == 3:
        orientation = 3
    return AssetSourceIdentity.create(
        identity.path,
        size_bytes=identity.size_bytes,
        source_mtime_ns=identity.source_mtime_ns,
        index_revision=identity.index_revision,
        width=size.width(),
        height=size.height(),
        orientation=orientation,
    )


def _raw_flip(raw: Any) -> int:
    try:
        return int(getattr(raw.sizes, "flip", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def _raw_visible_size(raw: Any, identity: AssetSourceIdentity) -> QSize:
    sizes = getattr(raw, "sizes", None)
    width = _positive_int(getattr(sizes, "iwidth", 0))
    height = _positive_int(getattr(sizes, "iheight", 0))
    if width <= 0 or height <= 0:
        width = _positive_int(getattr(sizes, "width", 0))
        height = _positive_int(getattr(sizes, "height", 0))
    if width <= 0 or height <= 0:
        width = max(0, int(identity.width))
        height = max(0, int(identity.height))
    if _raw_flip(raw) in (5, 6):
        width, height = height, width
    return QSize(width, height)


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _size_satisfies(candidate: QSize, target: QSize) -> bool:
    if candidate.isEmpty() or not candidate.isValid():
        return False
    return (
        candidate.width() >= target.width()
        and candidate.height() >= target.height()
    ) or (
        candidate.width() >= target.height()
        and candidate.height() >= target.width()
    )


def _raw_thumb_size(thumb: Any, rawpy: Any) -> QSize:
    if thumb is None:
        return QSize()
    if thumb.format == rawpy.ThumbFormat.JPEG:
        payload = QByteArray(bytes(thumb.data))
        buffer = QBuffer(payload)
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            return QSize()
        try:
            return QImageReader(buffer, b"JPEG").size()
        finally:
            buffer.close()
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        shape = getattr(thumb.data, "shape", ())
        if len(shape) >= 2:
            return QSize(_positive_int(shape[1]), _positive_int(shape[0]))
    return QSize()


def _qimage_from_raw_thumb(
    thumb: Any,
    rawpy: Any,
    target: QSize,
    *,
    orientation: int = 1,
) -> QImage:
    if thumb is None:
        return QImage()
    if thumb.format == rawpy.ThumbFormat.JPEG:
        payload = QByteArray(bytes(thumb.data))
        buffer = QBuffer(payload)
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            return QImage()
        try:
            reader = QImageReader(buffer, b"JPEG")
            reader.setAutoTransform(True)
            intrinsic = reader.size()
            if intrinsic.isValid() and not intrinsic.isEmpty():
                reader_target = target
                if orientation in (5, 6, 7, 8):
                    reader_target = QSize(target.height(), target.width())
                scaled = intrinsic.scaled(
                    reader_target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                )
                if scaled.width() < intrinsic.width() or scaled.height() < intrinsic.height():
                    reader.setScaledSize(scaled)
            return reader.read()
        finally:
            buffer.close()
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        return _qimage_from_array(thumb.data)
    return QImage()


def _postprocess_raw(raw: Any, *, half_size: bool) -> Any:
    try:
        return raw.postprocess(
            use_camera_wb=True,
            half_size=half_size,
            no_auto_bright=False,
            output_bps=8,
        )
    except Exception as exc:  # noqa: BLE001 - rawpy surfaces native codec failures variably
        raise RuntimeError(f"RAW {('half' if half_size else 'full')} decode failed: {exc}") from exc


def _qimage_from_array(array: Any) -> QImage:
    shape = getattr(array, "shape", ())
    strides = getattr(array, "strides", ())
    if len(shape) != 3 or shape[2] not in (3, 4) or len(strides) < 1:
        return QImage()
    try:
        height = int(shape[0])
        width = int(shape[1])
        stride = int(strides[0])
        image_format = (
            QImage.Format.Format_RGB888
            if int(shape[2]) == 3
            else QImage.Format.Format_RGBA8888
        )
        return QImage(array.data, width, height, stride, image_format).copy()
    except (AttributeError, BufferError, TypeError, ValueError):
        return QImage()


__all__ = [
    "CancellationToken",
    "DecodeCancelledError",
    "DecodedSurface",
    "DefaultStillDecodeBackend",
    "QtStillDecodeBackend",
    "RawStillDecodeBackend",
    "StillDecodeBackend",
    "StillDecodeBackendRegistry",
    "probe_raw_source_identity",
]
