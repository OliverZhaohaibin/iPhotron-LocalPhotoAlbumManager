"""Shared Pillow image loading helpers for local AI pipelines."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

_HEIF_REGISTERED = False


class ImageLoadError(RuntimeError):
    """Raised when an image cannot be decoded for local processing."""


def ensure_pillow_image_plugins() -> None:
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        _HEIF_REGISTERED = True
        return
    register_heif_opener()
    _HEIF_REGISTERED = True


def load_image_rgb(
    image_path: Path,
    *,
    error_cls: type[RuntimeError] = ImageLoadError,
) -> Image.Image:
    ensure_pillow_image_plugins()
    try:
        return _load_image_rgb(image_path, error_cls=error_cls)
    except OSError as exc:
        if not is_truncated_image_error(exc):
            raise error_cls(str(exc)) from exc

    previous_truncated_setting = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        return _load_image_rgb(image_path, error_cls=error_cls)
    except (OSError, UnidentifiedImageError) as exc:
        raise error_cls(str(exc)) from exc
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous_truncated_setting


def _load_image_rgb(
    image_path: Path,
    *,
    error_cls: type[RuntimeError],
) -> Image.Image:
    try:
        with Image.open(image_path) as image:
            corrected = ImageOps.exif_transpose(image)
            return corrected.convert("RGB")
    except UnidentifiedImageError as exc:
        raise error_cls(str(exc)) from exc


def is_truncated_image_error(exc: OSError) -> bool:
    return "image file is truncated" in str(exc).lower()


def pil_image_to_bgr(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image, dtype=np.uint8)
    return rgb[:, :, ::-1].copy()
