"""Image helpers for pet detection thumbnails and crops."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

_HEIF_REGISTERED = False


class PetImageLoadError(RuntimeError):
    """Raised when an asset cannot be decoded for pet processing."""


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


def load_image_rgb(image_path: Path) -> Image.Image:
    ensure_pillow_image_plugins()
    try:
        return _load_image_rgb(image_path)
    except OSError as exc:
        if "image file is truncated" not in str(exc).lower():
            raise PetImageLoadError(str(exc)) from exc

    previous_truncated_setting = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        return _load_image_rgb(image_path)
    except (OSError, UnidentifiedImageError) as exc:
        raise PetImageLoadError(str(exc)) from exc
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous_truncated_setting


def _load_image_rgb(image_path: Path) -> Image.Image:
    try:
        with Image.open(image_path) as image:
            corrected = ImageOps.exif_transpose(image)
            return corrected.convert("RGB")
    except UnidentifiedImageError as exc:
        raise PetImageLoadError(str(exc)) from exc


def padded_bbox(
    bbox: tuple[int, int, int, int],
    *,
    image_width: int,
    image_height: int,
    padding_ratio: float = 0.08,
) -> tuple[int, int, int, int]:
    x, y, width, height = bbox
    pad_x = round(width * padding_ratio)
    pad_y = round(height * padding_ratio)
    left = max(0, x - pad_x)
    top = max(0, y - pad_y)
    right = min(image_width, x + width + pad_x)
    bottom = min(image_height, y + height + pad_y)
    return left, top, max(1, right - left), max(1, bottom - top)


def crop_pet_region(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    *,
    padding_ratio: float = 0.08,
) -> Image.Image:
    image_width, image_height = image.size
    x, y, width, height = padded_bbox(
        bbox,
        image_width=image_width,
        image_height=image_height,
        padding_ratio=padding_ratio,
    )
    return image.crop((x, y, x + width, y + height))


def save_pet_thumbnail(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    output_path: Path,
    *,
    padding_ratio: float = 0.08,
    max_size: int = 320,
) -> Path:
    thumbnail = crop_pet_region(image, bbox, padding_ratio=padding_ratio)
    thumbnail.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.save(output_path, format="PNG")
    return output_path


def image_to_chw_float(image: Image.Image, size: tuple[int, int]) -> np.ndarray:
    resized = ImageOps.fit(image.convert("RGB"), size, Image.Resampling.BICUBIC)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    mean = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
    array = (array - mean) / std
    return np.transpose(array, (2, 0, 1))[None, :, :, :].astype(np.float32)
