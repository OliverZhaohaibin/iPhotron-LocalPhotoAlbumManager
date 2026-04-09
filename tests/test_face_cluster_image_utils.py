from __future__ import annotations

import os
import sys

from PIL import Image


_DEMO_FACE_CLUSTER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "demo", "face-cluster")
)
if _DEMO_FACE_CLUSTER not in sys.path:
    sys.path.insert(0, _DEMO_FACE_CLUSTER)

from image_utils import compute_square_crop_box, create_circular_thumbnail, crop_face_thumbnail


def test_compute_square_crop_box_clamps_to_image_bounds() -> None:
    crop_box = compute_square_crop_box((120, 90), (100, 10, 40, 30), padding_ratio=0.5)
    left, top, right, bottom = crop_box
    assert 0 <= left < right <= 120
    assert 0 <= top < bottom <= 90
    assert (right - left) == (bottom - top)


def test_crop_face_thumbnail_returns_square_image() -> None:
    image = Image.new("RGB", (200, 120), color=(180, 90, 60))
    thumbnail = crop_face_thumbnail(image, (70, 15, 40, 25), padding_ratio=0.2, min_size=64)
    assert thumbnail.width == thumbnail.height
    assert thumbnail.width >= 64


def test_create_circular_thumbnail_makes_corners_transparent() -> None:
    image = Image.new("RGB", (80, 80), color=(255, 0, 0))
    circular = create_circular_thumbnail(image, size=64)
    assert circular.mode == "RGBA"
    assert circular.size == (64, 64)
    assert circular.getpixel((0, 0))[3] == 0
    assert circular.getpixel((32, 32))[3] == 255
