
from unittest.mock import MagicMock, patch
from PySide6.QtGui import QImage
from PIL import Image
import pytest

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
