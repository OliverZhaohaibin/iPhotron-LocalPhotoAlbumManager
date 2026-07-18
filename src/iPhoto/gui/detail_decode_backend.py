"""Viewport-aware neutral still-image decode backends for Detail."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Protocol

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColorSpace, QImage, QImageReader

from iPhoto.core.raw_processor import is_raw_extension
from iPhoto.gui.detail_pipeline import (
    DetailDecodeKey,
    DetailDecodeLevel,
    DetailRenderRequest,
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


@dataclass(frozen=True, slots=True)
class DecodedSurface:
    """Detached neutral upload surface owned by one scheduler delivery."""

    image: QImage
    decode_key: DetailDecodeKey
    source_size: tuple[int, int]
    decoded_size: tuple[int, int]
    decode_level: DetailDecodeLevel
    backend: str
    fallback: str | None = None
    pixel_format: str = "rgba8888"
    color_space: str = "srgb"
    orientation_applied: bool = True
    cache_tier: Literal["decode", "memory", "disk"] = "decode"
    backing_owner: object | None = None


def _check_cancelled(token: CancellationToken) -> None:
    if token.is_cancelled():
        raise DecodeCancelledError("Still-image decode cancelled")


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

    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface:
        prepared = request.with_decode_level()
        target = _target_size(prepared)
        required = max(target.width(), target.height())
        _check_cancelled(cancellation)
        rawpy = _import_rawpy()
        if rawpy is None:
            raise RuntimeError("rawpy is unavailable")
        source = prepared.source_identity.path
        fallback: str | None = None
        image = QImage()
        with rawpy.imread(str(source)) as raw:
            _check_cancelled(cancellation)
            try:
                thumb = raw.extract_thumb()
                image = _qimage_from_raw_thumb(thumb, rawpy)
            except Exception:  # noqa: BLE001 - embedded previews are optional codec data
                image = QImage()
            if image.isNull() or max(image.width(), image.height()) < required:
                fallback = "half"
                image = _qimage_from_raw_rgb(raw, half_size=True)
            _check_cancelled(cancellation)
            if image.isNull() or max(image.width(), image.height()) < required:
                fallback = "full"
                image = _qimage_from_raw_rgb(raw, half_size=False)
        _check_cancelled(cancellation)
        if image.isNull():
            raise RuntimeError("RAW decoder returned an empty frame")
        surface = _normalise_surface(image, target)
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

    def __init__(self) -> None:
        self._qt = QtStillDecodeBackend()
        self._raw = RawStillDecodeBackend()

    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface:
        if is_raw_extension(request.source_identity.path.suffix):
            return self._raw.decode(request, cancellation)
        return self._qt.decode(request, cancellation)


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


def _qimage_from_raw_thumb(thumb: Any, rawpy: Any) -> QImage:
    if thumb is None:
        return QImage()
    if thumb.format == rawpy.ThumbFormat.JPEG:
        pillow = load_pillow()
        if pillow is not None:
            try:
                with pillow.Image.open(BytesIO(bytes(thumb.data))) as opened:
                    image = pillow.ImageOps.exif_transpose(opened)
                    return QImage(pillow.ImageQt(image.convert("RGBA"))).copy()
            except Exception:  # noqa: BLE001 - fall through to Qt's JPEG decoder
                return QImage.fromData(bytes(thumb.data), "JPEG")
        return QImage.fromData(bytes(thumb.data), "JPEG")
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        return _qimage_from_array(thumb.data)
    return QImage()


def _qimage_from_raw_rgb(raw: Any, *, half_size: bool) -> QImage:
    try:
        rgb = raw.postprocess(
            use_camera_wb=True,
            half_size=half_size,
            no_auto_bright=False,
            output_bps=8,
        )
    except Exception:  # noqa: BLE001 - rawpy surfaces native codec failures variably
        return QImage()
    return _qimage_from_array(rgb)


def _qimage_from_array(array: Any) -> QImage:
    pillow = load_pillow()
    if pillow is None:
        return QImage()
    try:
        image = pillow.Image.fromarray(array)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return QImage.fromData(buffer.getvalue(), "PNG")
    except Exception:  # noqa: BLE001 - numpy/Pillow availability is optional
        return QImage()


__all__ = [
    "CancellationToken",
    "DecodeCancelledError",
    "DecodedSurface",
    "DefaultStillDecodeBackend",
    "QtStillDecodeBackend",
    "RawStillDecodeBackend",
    "StillDecodeBackend",
]
