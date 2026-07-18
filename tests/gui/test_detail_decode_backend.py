from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from iPhoto.gui.detail_decode_backend import (
    DecodeCancelledError,
    QtStillDecodeBackend,
    RawStillDecodeBackend,
)
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailGeometryState,
    DetailRenderRequest,
)


class _Token:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


class _CountingToken:
    def __init__(self, cancel_at: int) -> None:
        self.cancel_at = cancel_at
        self.checks = 0

    def is_cancelled(self) -> bool:
        self.checks += 1
        return self.checks >= self.cancel_at


def _request(source: Path, *, level: int = 1024) -> DetailRenderRequest:
    return DetailRenderRequest(
        generation=1,
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(
            source,
            size_bytes=100,
            source_mtime_ns=1,
            width=4000,
            height=3000,
        ),
        viewport_physical_size=(800, 600),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
        decode_level=level,
    )


def test_qt_backend_returns_scaled_detached_rgba_surface(tmp_path: Path) -> None:
    source = tmp_path / "alpha.png"
    image = QImage(2400, 1200, QImage.Format.Format_RGBA8888)
    image.fill(0x80112233)
    assert image.save(str(source), "PNG")

    surface = QtStillDecodeBackend().decode(_request(source), _Token())

    assert surface.decoded_size == (1024, 512)
    assert surface.image.format() == QImage.Format.Format_RGBA8888
    assert surface.image.hasAlphaChannel()
    assert surface.color_space == "srgb"
    assert surface.backend == "qt"


def test_qt_backend_checks_cancellation_before_decode(tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    QImage(10, 10, QImage.Format.Format_RGBA8888).save(str(source), "PNG")
    try:
        QtStillDecodeBackend().decode(_request(source), _Token(cancelled=True))
    except DecodeCancelledError:
        pass
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("cancelled decode should not enter QImageReader")


def test_qt_backend_uses_pillow_fallback_after_plugin_failure(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"placeholder")
    fallback = QImage(800, 600, QImage.Format.Format_RGBA8888)

    class _FailingReader:
        def __init__(self, _source: str) -> None:
            pass

        def setCacheEnabled(self, _enabled: bool) -> None:
            pass

        def setAutoTransform(self, _enabled: bool) -> None:
            pass

        def size(self) -> QSize:
            return QSize()

        def read(self) -> QImage:
            return QImage()

        def errorString(self) -> str:
            return "plugin failed"

    with patch(
        "iPhoto.gui.detail_decode_backend.QImageReader",
        _FailingReader,
    ), patch(
        "iPhoto.gui.detail_decode_backend._load_with_pillow",
        return_value=fallback,
    ):
        surface = QtStillDecodeBackend().decode(_request(source), _Token())

    assert surface.fallback == "pillow"
    assert surface.decoded_size == (800, 600)


def test_qt_backend_marks_plugin_that_ignores_scaled_decode(tmp_path: Path) -> None:
    source = tmp_path / "ignored-scale.heic"
    source.write_bytes(b"placeholder")
    decoded = QImage(4000, 3000, QImage.Format.Format_RGBA8888)

    class _FullReader:
        def __init__(self, _source: str) -> None:
            pass

        def setCacheEnabled(self, _enabled: bool) -> None:
            pass

        def setAutoTransform(self, _enabled: bool) -> None:
            pass

        def size(self) -> QSize:
            return QSize(4000, 3000)

        def setScaledSize(self, _size: QSize) -> None:
            pass

        def read(self) -> QImage:
            return decoded

    with patch("iPhoto.gui.detail_decode_backend.QImageReader", _FullReader):
        surface = QtStillDecodeBackend().decode(_request(source), _Token())

    assert surface.fallback == "qt_full_scale"
    assert surface.decoded_size == (1024, 768)


def test_qt_backend_applies_exif_orientation_once(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (100, 50), "red")
    exif = image.getexif()
    exif[0x0112] = 6
    image.save(source, exif=exif)
    request = DetailRenderRequest(
        generation=1,
        asset_id="rotated",
        source_identity=AssetSourceIdentity.create(
            source,
            source_mtime_ns=1,
            width=50,
            height=100,
            orientation=6,
        ),
        viewport_physical_size=(50, 100),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
        decode_level=100,
    )

    surface = QtStillDecodeBackend().decode(request, _Token())

    assert surface.decoded_size == (50, 100)
    assert surface.orientation_applied is True


def test_raw_backend_prefers_adequate_embedded_preview(tmp_path: Path) -> None:
    source = tmp_path / "photo.dng"
    source.write_bytes(b"raw")
    preview_path = tmp_path / "preview.jpg"
    preview = QImage(1600, 1200, QImage.Format.Format_RGB32)
    assert preview.save(str(preview_path), "JPEG")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.extract_thumb.return_value = SimpleNamespace(
        format="jpeg",
        data=preview_path.read_bytes(),
    )
    rawpy = SimpleNamespace(
        ThumbFormat=SimpleNamespace(JPEG="jpeg", BITMAP="bitmap"),
        imread=MagicMock(return_value=raw),
    )

    with patch(
        "iPhoto.gui.detail_decode_backend._import_rawpy",
        return_value=rawpy,
    ):
        surface = RawStillDecodeBackend().decode(_request(source), _Token())

    assert surface.fallback is None
    assert surface.decoded_size == (1024, 768)
    raw.postprocess.assert_not_called()


def test_raw_backend_checks_cancellation_after_native_preview(tmp_path: Path) -> None:
    source = tmp_path / "cancelled.dng"
    source.write_bytes(b"raw")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.extract_thumb.return_value = SimpleNamespace(
        format="bitmap",
        data=np.zeros((1200, 1600, 3), dtype=np.uint8),
    )
    rawpy = SimpleNamespace(
        ThumbFormat=SimpleNamespace(JPEG="jpeg", BITMAP="bitmap"),
        imread=MagicMock(return_value=raw),
    )

    with patch("iPhoto.gui.detail_decode_backend._import_rawpy", return_value=rawpy):
        with pytest.raises(DecodeCancelledError):
            RawStillDecodeBackend().decode(_request(source), _CountingToken(cancel_at=3))

    raw.postprocess.assert_not_called()


def test_raw_backend_falls_through_half_to_full(tmp_path: Path) -> None:
    source = tmp_path / "photo.nef"
    source.write_bytes(b"raw")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.extract_thumb.return_value = SimpleNamespace(
        format="bitmap",
        data=np.zeros((100, 100, 3), dtype=np.uint8),
    )
    raw.postprocess.side_effect = [
        np.zeros((600, 800, 3), dtype=np.uint8),
        np.zeros((1200, 1600, 3), dtype=np.uint8),
    ]
    rawpy = SimpleNamespace(
        ThumbFormat=SimpleNamespace(JPEG="jpeg", BITMAP="bitmap"),
        imread=MagicMock(return_value=raw),
    )

    with patch(
        "iPhoto.gui.detail_decode_backend._import_rawpy",
        return_value=rawpy,
    ):
        surface = RawStillDecodeBackend().decode(_request(source), _Token())

    assert surface.fallback == "full"
    assert [call.kwargs["half_size"] for call in raw.postprocess.call_args_list] == [
        True,
        False,
    ]
    assert surface.decoded_size == (1024, 768)


def test_raw_backend_uses_adequate_half_size_demosaic(tmp_path: Path) -> None:
    source = tmp_path / "photo.cr3"
    source.write_bytes(b"raw")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.extract_thumb.side_effect = RuntimeError("no preview")
    raw.postprocess.return_value = np.zeros((1200, 1600, 3), dtype=np.uint8)
    rawpy = SimpleNamespace(
        ThumbFormat=SimpleNamespace(JPEG="jpeg", BITMAP="bitmap"),
        imread=MagicMock(return_value=raw),
    )

    with patch("iPhoto.gui.detail_decode_backend._import_rawpy", return_value=rawpy):
        surface = RawStillDecodeBackend().decode(_request(source), _Token())

    assert surface.fallback == "half"
    assert raw.postprocess.call_count == 1
    assert raw.postprocess.call_args.kwargs["half_size"] is True


def test_raw_backend_reports_corrupt_source_as_decode_failure(tmp_path: Path) -> None:
    source = tmp_path / "broken.nef"
    source.write_bytes(b"broken")
    rawpy = SimpleNamespace(imread=MagicMock(side_effect=RuntimeError("corrupt raw")))

    with patch("iPhoto.gui.detail_decode_backend._import_rawpy", return_value=rawpy):
        with pytest.raises(RuntimeError, match="corrupt raw"):
            RawStillDecodeBackend().decode(_request(source), _Token())
