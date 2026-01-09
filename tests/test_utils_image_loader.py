
from unittest.mock import MagicMock, patch
from PySide6.QtGui import QImage
from PIL import Image
import pytest
from io import BytesIO

from src.iPhoto.utils import image_loader

def test_qimage_from_pil_success():
    """Test successful conversion from PIL Image to QImage."""
    # Create a small RGB image
    pil_image = Image.new("RGB", (10, 10), color="red")

    qimg = image_loader.qimage_from_pil(pil_image)

    assert qimg is not None
    assert isinstance(qimg, QImage)
    assert qimg.width() == 10
    assert qimg.height() == 10
    # Check format (Pillow converts to RGBA before creation)
    # ImageQt typically produces ARGB32 or RGBA8888 depending on platform/version
    valid_formats = (QImage.Format.Format_RGBA8888, QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32)
    assert qimg.format() in valid_formats

def test_qimage_from_pil_handles_missing_imageqt(monkeypatch):
    """Test returns None if ImageQt is not available."""
    monkeypatch.setattr(image_loader, "_ImageQt", None)

    pil_image = Image.new("RGB", (10, 10))
    qimg = image_loader.qimage_from_pil(pil_image)

    assert qimg is None

def test_qimage_from_pil_handles_exception(monkeypatch):
    """Test returns None if conversion raises exception."""
    mock_image_qt = MagicMock(side_effect=Exception("Conversion failed"))
    monkeypatch.setattr(image_loader, "_ImageQt", mock_image_qt)

    pil_image = Image.new("RGB", (10, 10))
    qimg = image_loader.qimage_from_pil(pil_image)

    assert qimg is None

def test_qimage_from_pil_converts_to_rgba():
    """Test that image is converted to RGBA before QImage creation."""
    pil_image = Image.new("L", (10, 10)) # Grayscale

    with patch("src.iPhoto.utils.image_loader._ImageQt") as mock_qt:
        image_loader.qimage_from_pil(pil_image)

        # Check that the image passed to ImageQt was converted
        args, _ = mock_qt.call_args
        passed_image = args[0]
        assert passed_image.mode == "RGBA"

def test_generate_micro_thumbnail_success(tmp_path):
    """Test generating a micro thumbnail from a valid image."""
    image_path = tmp_path / "test.jpg"
    # Create 100x50 image
    img = Image.new("RGB", (100, 50), color="blue")
    img.save(image_path, format="JPEG")

    blob = image_loader.generate_micro_thumbnail(image_path)

    assert blob is not None
    assert isinstance(blob, bytes)
    assert len(blob) > 0

    # Verify blob is a valid JPEG
    thumb = Image.open(BytesIO(blob))
    assert thumb.format == "JPEG"
    # Verify dimensions: 100x50 -> max 16 -> 16x8
    assert thumb.size == (16, 8)

def test_generate_micro_thumbnail_preserves_aspect_ratio(tmp_path):
    """Test that aspect ratio is preserved during scaling."""
    image_path = tmp_path / "tall.jpg"
    # Create 50x100 image
    img = Image.new("RGB", (50, 100), color="green")
    img.save(image_path, format="JPEG")

    blob = image_loader.generate_micro_thumbnail(image_path)

    assert blob is not None
    thumb = Image.open(BytesIO(blob))
    # 50x100 -> max 16 -> 8x16
    assert thumb.size == (8, 16)

def test_generate_micro_thumbnail_converts_to_rgb(tmp_path):
    """Test that RGBA images are converted to RGB for JPEG compatibility."""
    image_path = tmp_path / "alpha.png"
    img = Image.new("RGBA", (20, 20), color=(255, 0, 0, 128))
    img.save(image_path, format="PNG")

    blob = image_loader.generate_micro_thumbnail(image_path)

    assert blob is not None
    thumb = Image.open(BytesIO(blob))
    assert thumb.mode == "RGB"
    assert thumb.format == "JPEG"

def test_generate_micro_thumbnail_handles_missing_dependencies(monkeypatch, tmp_path):
    """Test returns None if Pillow dependencies are missing."""
    monkeypatch.setattr(image_loader, "_Image", None)
    image_path = tmp_path / "test.jpg"
    # File doesn't even need to exist if dependency check fails first

    blob = image_loader.generate_micro_thumbnail(image_path)
    assert blob is None

def test_generate_micro_thumbnail_handles_io_errors(tmp_path):
    """Test handles file not found or invalid image gracefully."""
    non_existent = tmp_path / "ghost.jpg"
    blob = image_loader.generate_micro_thumbnail(non_existent)
    assert blob is None

    invalid_file = tmp_path / "broken.jpg"
    invalid_file.write_text("not an image")
    blob = image_loader.generate_micro_thumbnail(invalid_file)
    assert blob is None
