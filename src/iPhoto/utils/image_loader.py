"""Helpers for loading Qt image primitives with Pillow fallbacks."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

from ..core.raw_processor import is_raw_extension, load_raw_to_pil
from .deps import load_pillow

if TYPE_CHECKING:
    from PIL import Image

_PILLOW = load_pillow()
if _PILLOW is not None:  # pragma: no branch - import guard
    _Image = _PILLOW.Image
    _ImageOps = _PILLOW.ImageOps
    _ImageQt = _PILLOW.ImageQt
else:  # pragma: no cover - executed when Pillow is unavailable
    _Image = None  # type: ignore[assignment]
    _ImageOps = None  # type: ignore[assignment]
    _ImageQt = None  # type: ignore[assignment]

_LOGGER = logging.getLogger(__name__)

# Qt image plugins execute native code before Python can recover from decoder
# failures.  In particular, querying a JPEG reader's size has crashed headless
# Linux processes after other Qt plugins were exercised.  Pillow is already a
# required runtime dependency and gives us a Python-level failure boundary for
# the formats it decodes natively.  Keep Qt for formats where its platform
# plugins add compatibility (for example HEIC on some installations).
_PILLOW_NATIVE_EXTENSIONS = frozenset(
    {
        ".bmp",
        ".gif",
        ".jfif",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
)


def load_qimage(source: Path, target: QSize | None = None) -> Optional[QImage]:
    """Return a :class:`QImage` for *source* with optional scaling."""

    if not source.exists():
        _LOGGER.debug("Skipping image load for missing path: %s", source)
        return None

    # ── RAW fast-path ────────────────────────────────────────────────────
    # Qt cannot decode RAW camera files so we delegate to rawpy immediately
    # instead of letting QImageReader silently fail and falling through to
    # the slower Pillow path.
    if is_raw_extension(source.suffix):
        return _load_raw_qimage(source, target)

    # Do not hand malformed files in Pillow-native formats to Qt as a fallback:
    # native image plugins can terminate the process instead of returning a
    # null QImage.  If Pillow itself is unavailable, retain the Qt compatibility
    # path so installations with a partially broken optional Pillow stack can
    # still display images.
    pillow_qimage_available = _Image is not None and _ImageOps is not None and _ImageQt is not None
    if source.suffix.casefold() in _PILLOW_NATIVE_EXTENSIONS and pillow_qimage_available:
        return _load_with_pillow(source, target)

    return _load_with_qt(source, target)


def _load_with_qt(source: Path, target: QSize | None = None) -> Optional[QImage]:
    """Decode formats that require Qt platform image plugins."""

    # ``QImageReader`` is most efficient when it can stream directly from the
    # filename because platform formats such as HEIC expose fast-paths for
    # downscaling during decode.  Reading the bytes eagerly would defeat those
    # optimisations, so this compatibility path hands the path directly to Qt.
    reader = QImageReader(str(source))
    # Qt maintains a process-wide image cache that is enabled by default.
    # Large libraries can end up decoding hundreds of images during a single
    # browsing session which would otherwise accumulate in that cache.  The
    # additional allocations not only increase peak memory usage but can also
    # hold operating system file handles open.  Older PySide6 builds do not
    # expose ``setCacheEnabled`` though, so we guard the call to keep the code
    # compatible with those runtimes while still disabling the cache whenever
    # the API is available.
    disable_cache = getattr(reader, "setCacheEnabled", None)
    if callable(disable_cache):
        disable_cache(False)
    reader.setAutoTransform(True)
    if target is not None and target.isValid() and not target.isEmpty():
        original_size = reader.size()
        if original_size.isValid() and not original_size.isEmpty():
            # ``QImageReader.setScaledSize`` always interprets the requested
            # dimensions literally, even when that would distort the image.  We
            # pre-compute a size that preserves the source aspect ratio so the
            # decoder performs a proportional downscale rather than stretching to
            # fill the viewport bounds supplied by the caller.
            scaled_target = original_size.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            # Only request scaling when the destination is genuinely smaller; this
            # avoids unnecessary interpolation for thumbnails that are already
            # below the desired output resolution.
            if (
                scaled_target.width() < original_size.width()
                or scaled_target.height() < original_size.height()
            ):
                reader.setScaledSize(scaled_target)
        else:
            # Some formats only disclose their intrinsic size during ``read``. In
            # those cases we skip ``setScaledSize`` entirely to avoid guessing an
            # aspect ratio that might be wildly incorrect.  The caller will still
            # downscale the resulting pixmap once Qt reports the true dimensions.
            pass
    image = reader.read()
    if not image.isNull():
        return image
    return _load_with_pillow(source, target)


def load_qpixmap(source: Path, target: QSize | None = None) -> Optional[QPixmap]:
    """Return a :class:`QPixmap` for *source*, falling back to Pillow when required."""

    image = load_qimage(source, target)
    if image is None or image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        return None
    return pixmap


def qimage_from_bytes(data: bytes) -> Optional[QImage]:
    """Return a :class:`QImage` decoded from JPEG/PNG *data*."""

    # Avoid handing arbitrary/corrupt index BLOBs to platform image plugins.
    # Some Qt/PySide builds can crash natively instead of returning a null image.
    if data.startswith(b"\xff\xd8\xff"):
        image_format = "JPEG"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        image_format = "PNG"
    elif len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        image_format = "WEBP"
    else:
        return None

    pillow_owns_format = (
        _Image is not None
        and _ImageOps is not None
        and _ImageQt is not None
        and _pillow_supports_format(image_format)
    )
    if pillow_owns_format:
        try:
            with _Image.open(BytesIO(data)) as img:  # type: ignore[union-attr]
                img = _ImageOps.exif_transpose(img)
                qt_image = _ImageQt(img.convert("RGBA"))
                return QImage(qt_image).copy()
        except Exception:
            _LOGGER.debug("Pillow failed to decode image bytes in qimage_from_bytes")
            return None

    # Qt remains a compatibility decoder only when the Pillow-to-QImage bridge
    # is unavailable.  Once Pillow has accepted responsibility for these native
    # formats, a decode failure must not be retried inside a native Qt plugin.
    image = QImage()
    if image.loadFromData(data):
        return image
    if image.loadFromData(data, "JPEG"):
        return image
    if image.loadFromData(data, "JPG"):
        return image
    if image.loadFromData(data, "PNG"):
        return image
    return None


def _pillow_supports_format(image_format: str) -> bool:
    if _Image is None:
        return False
    try:
        initialize = getattr(_Image, "init", None)
        if callable(initialize):
            initialize()
        return str(image_format).upper() in getattr(_Image, "OPEN", {})
    except Exception:
        return False


def qimage_from_pil(image: "Image.Image") -> Optional[QImage]:
    """Return a :class:`QImage` from a PIL Image."""
    if _ImageQt is None:
        return None
    try:
        qt_image = _ImageQt(image.convert("RGBA"))
        return QImage(qt_image).copy()
    except Exception:
        _LOGGER.exception("Failed to convert PIL image to QImage")
        return None


def _load_with_pillow(source: Path, target: QSize | None = None) -> Optional[QImage]:
    if _Image is None or _ImageOps is None or _ImageQt is None:
        return None
    try:
        with _Image.open(source) as img:  # type: ignore[attr-defined]
            img = _ImageOps.exif_transpose(img)  # type: ignore[attr-defined]
            if target is not None and target.isValid() and not target.isEmpty():
                resample = getattr(_Image, "Resampling", _Image)
                resample_filter = getattr(resample, "LANCZOS", _Image.BICUBIC)
                img.thumbnail((target.width(), target.height()), resample_filter)
            qt_image = _ImageQt(img.convert("RGBA"))  # type: ignore[attr-defined]
            return QImage(qt_image).copy()
    except Exception:
        _LOGGER.exception("Pillow failed to load image from %s", source)
        return None


def _load_raw_qimage(source: Path, target: QSize | None = None) -> Optional[QImage]:
    """Decode a RAW file via rawpy and return as :class:`QImage`."""

    if _ImageQt is None:
        return None

    target_size = None
    half_size = False
    if target is not None and target.isValid() and not target.isEmpty():
        target_size = (target.width(), target.height())
        # For small targets (thumbnails) always request half-size decoding for speed.
        if target.width() <= 1024 and target.height() <= 1024:
            half_size = True

    pil_img = load_raw_to_pil(source, half_size=half_size, target_size=target_size)
    if pil_img is None:
        return None

    # Downscale to the requested bounding box after decode.
    if target_size is not None:
        resample = getattr(_Image, "Resampling", _Image)
        resample_filter = getattr(resample, "LANCZOS", _Image.BICUBIC)
        pil_img.thumbnail(target_size, resample_filter)

    try:
        qt_image = _ImageQt(pil_img.convert("RGBA"))
        return QImage(qt_image).copy()
    except Exception:
        _LOGGER.exception("Failed to convert RAW PIL image to QImage for %s", source)
        return None


def generate_micro_thumbnail(source: Path) -> Optional[bytes]:
    """Generate a 16x16 (max dimension) JPEG thumbnail bytes for the given image.

    This function loads the image using Pillow, scales it down maintaining aspect ratio
    such that the longest side is 16 pixels, and encodes it as a JPEG.
    """
    if _Image is None or _ImageOps is None:
        return None

    if not source.exists():
        return None

    # ── RAW fast-path ────────────────────────────────────────────────────
    if is_raw_extension(source.suffix):
        return _generate_raw_micro_thumbnail(source)

    try:
        with _Image.open(source) as img:  # type: ignore[attr-defined]
            # Optimization: Use draft mode for JPEG images to speed up loading
            # We request 64x64 to have enough headroom for high-quality downscaling
            # to the target 16x16 size while avoiding loading the full resolution image.
            if img.format == "JPEG":
                img.draft("RGB", (64, 64))

            # Scale to 16px max dimension
            target_size = (16, 16)
            resample = getattr(_Image, "Resampling", _Image)
            # Use BICUBIC instead of LANCZOS for speed; quality difference is negligible at 16x16
            resample_filter = getattr(resample, "BICUBIC", _Image.BICUBIC)
            # Optimization: Call thumbnail() BEFORE exif_transpose().
            # thumbnail() reduces the image dimensions in-place (often triggering the load).
            # If we transpose first (which creates a copy), we might be allocating a full-res
            # rotated copy of a large image (e.g. 20MP PNG), which is slow and memory-heavy.
            # By thumbnailing first, we only transpose a tiny 16x16 image.
            # This ordering is safe for any rectangular target box: thumbnail() fits the image
            # into a bounding box while preserving aspect ratio, and exif_transpose() only
            # rotates/flips the image (swapping width/height), so the final dimensions match
            # regardless of the order.
            img.thumbnail(target_size, resample_filter)

            # Handle orientation
            img = _ImageOps.exif_transpose(img)  # type: ignore[attr-defined]

            # Convert to RGB to ensure JPEG compatibility (drop alpha if present)
            # We convert AFTER resizing to avoid expensive RGB conversion on full-res images (e.g. RGBA PNGs)
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Save to bytes
            output = BytesIO()
            img.save(output, format="JPEG", quality=75)
            return output.getvalue()
    except Exception:
        _LOGGER.debug("Failed to generate micro thumbnail for %s", source, exc_info=True)
        return None


def _generate_raw_micro_thumbnail(source: Path) -> Optional[bytes]:
    """Generate a micro thumbnail for a RAW camera file."""

    pil_img = load_raw_to_pil(source, half_size=True)
    if pil_img is None:
        return None

    try:
        target_size = (16, 16)
        resample = getattr(_Image, "Resampling", _Image)
        resample_filter = getattr(resample, "BICUBIC", _Image.BICUBIC)
        pil_img.thumbnail(target_size, resample_filter)

        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")

        output = BytesIO()
        pil_img.save(output, format="JPEG", quality=75)
        return output.getvalue()
    except Exception:
        _LOGGER.debug("Failed to generate RAW micro thumbnail for %s", source, exc_info=True)
        return None
