"""Thread-safe QImage scaling helpers for background workers."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from ....utils.deps import load_pillow

_LOGGER = logging.getLogger(__name__)
_PILLOW = load_pillow()
_IMAGE = _PILLOW.Image if _PILLOW is not None else None


def scale_qimage_to_height_for_worker(source: QImage, target_height: int) -> QImage:
    """Return *source* scaled to *target_height* without using Qt transforms on macOS."""

    if source.isNull():
        return QImage()

    requested_height = max(1, int(target_height))
    if sys.platform == "darwin":
        scaled = _pillow_scaled_to_height(source, requested_height)
        if scaled is not None and not scaled.isNull():
            return scaled

        # The macOS crash reports point at QImage::scaledToHeight inside worker
        # threads, so keep this fallback intentionally simple and unscaled.
        _LOGGER.warning("Falling back to unscaled worker preview because Pillow scaling failed")
        return _format_normalized_copy(source)

    return _qt_scaled_to_height(source, requested_height)


def _qt_scaled_to_height(source: QImage, target_height: int) -> QImage:
    scaled = source.scaledToHeight(
        target_height,
        Qt.TransformationMode.SmoothTransformation,
    )
    if scaled.isNull():
        scaled = QImage(source)
    return scaled.convertToFormat(QImage.Format.Format_ARGB32)


def _pillow_scaled_to_height(source: QImage, target_height: int) -> QImage | None:
    if _IMAGE is None:
        return None

    rgba = source.convertToFormat(QImage.Format.Format_RGBA8888)
    width = rgba.width()
    height = rgba.height()
    if width <= 0 or height <= 0:
        return None

    target_width = max(1, int(round(width * (target_height / float(height)))))

    try:
        data = _qimage_bytes(rgba)
        pil_image = _IMAGE.frombytes(
            "RGBA",
            (width, height),
            data,
            "raw",
            "RGBA",
            rgba.bytesPerLine(),
            1,
        )
        resample = getattr(_IMAGE, "Resampling", _IMAGE)
        resample_filter = getattr(resample, "LANCZOS", _IMAGE.BICUBIC)
        resized = pil_image.resize((target_width, target_height), resample_filter)
        raw = resized.tobytes("raw", "RGBA")
        qimage = QImage(
            raw,
            target_width,
            target_height,
            target_width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
    except Exception:
        _LOGGER.exception("Pillow failed to scale worker QImage preview")
        return None

    return qimage.convertToFormat(QImage.Format.Format_ARGB32)


def _qimage_bytes(image: QImage) -> bytes:
    bits = image.constBits()
    size = image.sizeInBytes()
    try:
        return bytes(bits[:size])
    except TypeError:
        set_size = getattr(bits, "setsize", None)
        if callable(set_size):
            set_size(size)
        return bytes(bits)


def _format_normalized_copy(source: QImage) -> QImage:
    if source.format() == QImage.Format.Format_ARGB32:
        return QImage(source)
    return source.convertToFormat(QImage.Format.Format_ARGB32)


__all__ = ["scale_qimage_to_height_for_worker"]
