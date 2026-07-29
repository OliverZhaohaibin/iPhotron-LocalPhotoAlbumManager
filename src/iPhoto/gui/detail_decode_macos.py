"""Optional ImageIO/CoreGraphics still decoder for packaged macOS builds."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QImage


class MacOSImageIOStillDecodeBackend:
    name = "imageio"

    def __init__(self, quartz: Any, foundation: Any) -> None:
        self._quartz = quartz
        self._foundation = foundation

    def decode(self, request, cancellation):
        from iPhoto.gui.detail_decode_backend import (
            DecodedSurface,
            _check_cancelled,
            _normalise_surface,
            _target_size,
        )
        from iPhoto.gui.detail_pipeline import DetailDecodeKey

        prepared = request.with_decode_level()
        target = _target_size(prepared)
        _check_cancelled(cancellation)
        url = self._foundation.NSURL.fileURLWithPath_(
            str(prepared.source_identity.path)
        )
        source = self._quartz.CGImageSourceCreateWithURL(url, None)
        if source is None:
            raise RuntimeError("ImageIO could not create an image source")
        options = {
            self._quartz.kCGImageSourceCreateThumbnailFromImageAlways: True,
            self._quartz.kCGImageSourceCreateThumbnailWithTransform: True,
            self._quartz.kCGImageSourceThumbnailMaxPixelSize: max(
                target.width(),
                target.height(),
            ),
        }
        cg_image = self._quartz.CGImageSourceCreateThumbnailAtIndex(
            source,
            0,
            options,
        )
        _check_cancelled(cancellation)
        if cg_image is None:
            raise RuntimeError("ImageIO could not decode a thumbnail surface")
        width = int(self._quartz.CGImageGetWidth(cg_image))
        height = int(self._quartz.CGImageGetHeight(cg_image))
        if width <= 0 or height <= 0:
            raise RuntimeError("ImageIO returned an empty surface")
        stride = width * 4
        pixels = bytearray(stride * height)
        color_space = self._quartz.CGColorSpaceCreateWithName(
            self._quartz.kCGColorSpaceSRGB
        )
        bitmap_info = (
            self._quartz.kCGImageAlphaPremultipliedLast
            | self._quartz.kCGBitmapByteOrder32Big
        )
        context = self._quartz.CGBitmapContextCreate(
            pixels,
            width,
            height,
            8,
            stride,
            color_space,
            bitmap_info,
        )
        if context is None:
            raise RuntimeError("CoreGraphics could not allocate a bitmap context")
        self._quartz.CGContextDrawImage(
            context,
            self._quartz.CGRectMake(0, 0, width, height),
            cg_image,
        )
        image = QImage(
            bytes(pixels),
            width,
            height,
            stride,
            QImage.Format.Format_RGBA8888,
        ).copy()
        surface = _normalise_surface(image, target)
        _check_cancelled(cancellation)
        if surface.isNull():
            raise RuntimeError("ImageIO returned an unusable RGBA surface")
        return DecodedSurface(
            image=surface,
            decode_key=DetailDecodeKey.from_request(prepared),
            source_size=(
                max(1, prepared.source_identity.width or width),
                max(1, prepared.source_identity.height or height),
            ),
            decoded_size=(surface.width(), surface.height()),
            decode_level=prepared.decode_level or "full",
            backend=self.name,
        )


def create_macos_imageio_backend() -> MacOSImageIOStillDecodeBackend | None:
    try:
        import Foundation  # type: ignore[import-not-found]
        import Quartz  # type: ignore[import-not-found]
    except ImportError:
        return None
    return MacOSImageIOStillDecodeBackend(Quartz, Foundation)


__all__ = ["MacOSImageIOStillDecodeBackend", "create_macos_imageio_backend"]

