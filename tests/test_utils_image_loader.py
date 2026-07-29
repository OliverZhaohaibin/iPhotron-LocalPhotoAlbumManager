from io import BytesIO
from unittest.mock import MagicMock, patch

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage

from iPhoto.utils import image_loader


def test_load_qimage_uses_pillow_for_jpeg_without_entering_qt_reader(tmp_path):
    image_path = tmp_path / "photo.jpg"
    Image.new("RGB", (80, 40), "red").save(image_path)

    with patch.object(
        image_loader,
        "QImageReader",
        side_effect=AssertionError("Pillow-native JPEG must not enter Qt plugins"),
    ):
        loaded = image_loader.load_qimage(image_path, QSize(20, 20))

    assert loaded is not None and not loaded.isNull()
    assert (loaded.width(), loaded.height()) == (20, 10)


def test_load_qimage_does_not_send_corrupt_jpeg_to_qt_reader(tmp_path):
    image_path = tmp_path / "broken.jpg"
    image_path.write_bytes(b"not a jpeg")

    with patch.object(
        image_loader,
        "QImageReader",
        side_effect=AssertionError("corrupt JPEG must remain inside the safe decoder boundary"),
    ):
        loaded = image_loader.load_qimage(image_path, QSize(20, 20))

    assert loaded is None


def test_load_qimage_retains_qt_fallback_when_pillow_bridge_is_unavailable(
    monkeypatch,
    tmp_path,
):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"decoder input")
    expected = QImage(4, 4, QImage.Format.Format_RGB32)
    qt_decode = MagicMock(return_value=expected)
    monkeypatch.setattr(image_loader, "_ImageQt", None)
    monkeypatch.setattr(image_loader, "_load_with_qt", qt_decode)

    loaded = image_loader.load_qimage(image_path)

    assert loaded is expected
    qt_decode.assert_called_once_with(image_path, None)


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
    valid_formats = (
        QImage.Format.Format_RGBA8888,
        QImage.Format.Format_RGB32,
        QImage.Format.Format_ARGB32,
    )
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


def test_qimage_from_pil_converts_to_rgba(monkeypatch):
    """Test that image is converted to RGBA before QImage creation."""
    pil_image = Image.new("L", (10, 10))  # Grayscale
    captured_modes: list[str] = []

    def fake_image_qt(image):
        captured_modes.append(image.mode)
        result = QImage(10, 10, QImage.Format.Format_RGBA8888)
        result.fill(0)
        return result

    monkeypatch.setattr(image_loader, "_ImageQt", fake_image_qt)

    result = image_loader.qimage_from_pil(pil_image)

    assert result is not None
    assert captured_modes == ["RGBA"]


def test_qimage_from_pil_detaches_from_temporary_imageqt_storage():
    class SharedImageQt(QImage):
        instances = []

        def __init__(self, _image):
            super().__init__(8, 8, QImage.Format.Format_RGBA8888)
            self.fill(QColor("red"))
            self.instances.append(self)

    pil_image = Image.new("RGB", (8, 8), color="red")
    with patch.object(image_loader, "_ImageQt", SharedImageQt):
        qimg = image_loader.qimage_from_pil(pil_image)

    assert qimg is not None
    SharedImageQt.instances[0].fill(QColor("blue"))
    assert qimg.pixelColor(4, 4).red() > 200
    assert qimg.pixelColor(4, 4).blue() < 50


def test_qimage_from_bytes_returns_none_when_pillow_decode_fails(monkeypatch):
    """Return None when neither Pillow nor Qt can decode broken data."""
    monkeypatch.setattr(
        image_loader,
        "_ImageQt",
        MagicMock(side_effect=Exception("Conversion failed")),
    )

    qimg = image_loader.qimage_from_bytes(b"not-an-image")

    assert qimg is None


def test_qimage_from_bytes_does_not_enter_qt_when_pillow_decode_fails(monkeypatch):
    monkeypatch.setattr(
        image_loader._Image,
        "open",
        MagicMock(side_effect=OSError("Pillow rejected payload")),
    )
    qt_image = MagicMock(side_effect=AssertionError("corrupt native format reached Qt"))
    monkeypatch.setattr(image_loader, "QImage", qt_image)

    qimg = image_loader.qimage_from_bytes(b"\xff\xd8\xffcorrupt-image")

    assert qimg is None
    qt_image.assert_not_called()


def test_qimage_from_bytes_uses_qt_when_pillow_bridge_is_unavailable(monkeypatch):
    class FakeQImage:
        def __init__(self, *_args):
            self.loaded = False

        def loadFromData(self, *_args):  # noqa: N802 - mirrors the Qt API
            self.loaded = True
            return True

    monkeypatch.setattr(image_loader, "_ImageQt", None)
    monkeypatch.setattr(image_loader, "QImage", FakeQImage)

    qimg = image_loader.qimage_from_bytes(b"\xff\xd8\xffqt-supported-image")

    assert isinstance(qimg, FakeQImage)
    assert qimg.loaded


def test_qimage_from_bytes_uses_qt_when_pillow_does_not_support_format(monkeypatch):
    class FakeQImage:
        def __init__(self, *_args):
            self.loaded = False

        def loadFromData(self, *_args):  # noqa: N802 - mirrors the Qt API
            self.loaded = True
            return True

    monkeypatch.setattr(image_loader, "_pillow_supports_format", lambda _format: False)
    monkeypatch.setattr(image_loader, "QImage", FakeQImage)

    qimg = image_loader.qimage_from_bytes(b"RIFF\x04\x00\x00\x00WEBP")

    assert isinstance(qimg, FakeQImage)
    assert qimg.loaded


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
