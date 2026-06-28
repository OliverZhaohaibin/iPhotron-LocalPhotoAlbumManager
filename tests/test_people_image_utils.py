from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFile

from iPhoto.people.image_utils import load_image_rgb


def test_load_image_rgb_accepts_truncated_jpeg_without_leaking_pillow_flag(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "truncated.jpg"
    buffer = BytesIO()
    Image.new("RGB", (24, 18), color=(120, 80, 40)).save(buffer, format="JPEG")
    image_path.write_bytes(buffer.getvalue()[:-14])
    previous = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = False

    try:
        loaded = load_image_rgb(image_path)
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous

    assert loaded.mode == "RGB"
    assert loaded.size == (24, 18)
    assert ImageFile.LOAD_TRUNCATED_IMAGES is False
