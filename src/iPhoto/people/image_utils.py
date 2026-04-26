"""Image helpers shared by the People feature."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

_HEIF_REGISTERED = False


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
    with Image.open(image_path) as image:
        corrected = ImageOps.exif_transpose(image)
        return corrected.convert("RGB")


def pil_image_to_bgr(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image, dtype=np.uint8)
    return rgb[:, :, ::-1].copy()


def compute_square_crop_box(
    image_size: tuple[int, int],
    bbox: tuple[int, int, int, int],
    padding_ratio: float = 0.35,
) -> tuple[int, int, int, int]:
    image_width, image_height = image_size
    box_x, box_y, box_w, box_h = bbox

    padding = int(round(max(box_w, box_h) * padding_ratio))
    center_x = box_x + box_w / 2.0
    center_y = box_y + box_h / 2.0
    side = int(round(max(box_w, box_h) + padding * 2))
    side = max(8, min(side, max(image_width, image_height)))

    left = int(round(center_x - side / 2.0))
    top = int(round(center_y - side / 2.0))
    left = max(0, min(left, image_width - side))
    top = max(0, min(top, image_height - side))
    right = min(image_width, left + side)
    bottom = min(image_height, top + side)

    if right - left != bottom - top:
        side = min(right - left, bottom - top)
        right = left + side
        bottom = top + side
    return left, top, right, bottom


def crop_face_thumbnail(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    padding_ratio: float = 0.35,
    min_size: int = 160,
) -> Image.Image:
    crop_box = compute_square_crop_box(image.size, bbox, padding_ratio=padding_ratio)
    cropped = image.crop(crop_box)
    if min(cropped.size) < min_size:
        cropped = cropped.resize((min_size, min_size), Image.Resampling.LANCZOS)
    return cropped


def save_face_thumbnail(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    output_path: Path,
    padding_ratio: float = 0.35,
    min_size: int = 160,
) -> Path:
    thumbnail = crop_face_thumbnail(
        image,
        bbox,
        padding_ratio=padding_ratio,
        min_size=min_size,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.save(output_path, format="PNG")
    return output_path


def create_circular_thumbnail(image: Image.Image, size: int = 112) -> Image.Image:
    square = ImageOps.fit(image.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    square.putalpha(mask)
    return square


def create_cover_thumbnail(
    image: Image.Image,
    size: tuple[int, int],
) -> Image.Image:
    width, height = size
    return ImageOps.fit(
        image.convert("RGBA"),
        (int(width), int(height)),
        Image.Resampling.LANCZOS,
    )
