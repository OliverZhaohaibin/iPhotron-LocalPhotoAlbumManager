
import io
from PIL import Image
from iPhoto.utils.image_loader import generate_micro_thumbnail
import tempfile
import os
from pathlib import Path

def create_test_image_with_exif(orientation=6):
    """
    Create a 100x50 red image with EXIF orientation.
    Orientation 6 means rotated 90 CW.
    """
    img = Image.new("RGB", (100, 50), "red")
    exif = img.getexif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    buf.seek(0)
    return buf

def test_generate_micro_thumbnail_preserves_orientation():
    # Create an image that is 100x50 but rotated 90 degrees (so it looks like 50x100)
    source_buf = create_test_image_with_exif(orientation=6)

    # Write to a temporary file

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(source_buf.getvalue())
        tmp_path = tmp.name

    try:
        thumb_bytes = generate_micro_thumbnail(Path(tmp_path))

        assert thumb_bytes is not None

        with Image.open(io.BytesIO(thumb_bytes)) as thumb:
            # The thumbnail should be 16x16 max dimension.
            # 100x50 -> 16x8 (landscape).
            # Rotated 90 deg -> 8x16 (portrait).

            # Check dimensions. Since we optimized by swapping order, we expect the visual orientation to be correct.
            # Visual orientation: 50x100. Scaled to fit 16x16 -> 8x16.

            # Note: generate_micro_thumbnail saves as JPEG without EXIF usually (unless copied),
            # but it rotates the image pixels. So the resulting image should have dimensions that match the visual orientation.

            # 8x16 is the correct visual aspect ratio for a 50x100 visual image.
            assert thumb.size == (8, 16)

    finally:
        os.unlink(tmp_path)

def test_generate_micro_thumbnail_small_png():
    # Test with a PNG (no draft support)
    img = Image.new("RGB", (100, 100), "blue")

    import tempfile
    import os
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, format="PNG")
        tmp_path = tmp.name

    try:
        from pathlib import Path
        thumb_bytes = generate_micro_thumbnail(Path(tmp_path))
        assert thumb_bytes is not None

        with Image.open(io.BytesIO(thumb_bytes)) as thumb:
            assert thumb.size == (16, 16)
    finally:
        os.unlink(tmp_path)
