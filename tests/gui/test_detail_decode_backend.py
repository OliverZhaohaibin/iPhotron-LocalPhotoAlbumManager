from __future__ import annotations

import gc
import sys
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
    DefaultStillDecodeBackend,
    QtStillDecodeBackend,
    RawStillDecodeBackend,
    StillDecodeBackendRegistry,
    _qimage_from_array,
    _qimage_from_raw_thumb,
    probe_raw_source_identity,
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


def test_target_surface_is_capped_to_gpu_residency_budget(tmp_path: Path) -> None:
    source = tmp_path / "large-square.png"
    request = DetailRenderRequest(
        generation=1,
        asset_id="large",
        source_identity=AssetSourceIdentity.create(
            source,
            size_bytes=100,
            source_mtime_ns=1,
            width=9000,
            height=9000,
        ),
        viewport_physical_size=(8000, 8000),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="zoom",
        decode_level="full",
        texture_limit=8192,
    )
    from iPhoto.gui.detail_decode_backend import _target_size

    target = _target_size(request)

    assert target.width() * target.height() * 4 <= 192 * 1024 * 1024


def test_qt_backend_checks_cancellation_before_decode(tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    QImage(10, 10, QImage.Format.Format_RGBA8888).save(str(source), "PNG")
    try:
        QtStillDecodeBackend().decode(_request(source), _Token(cancelled=True))
    except DecodeCancelledError:
        pass
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("cancelled decode should not enter QImageReader")


def test_platform_registry_prefers_macos_backend_and_falls_back_to_qt(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.png"
    image = QImage(64, 32, QImage.Format.Format_RGBA8888)
    assert image.save(str(source), "PNG")

    class _FailingImageIO:
        def decode(self, request, cancellation):
            del request, cancellation
            raise RuntimeError("native decoder unavailable")

    registry = StillDecodeBackendRegistry(
        platform="darwin",
        macos_backend=_FailingImageIO(),
    )
    surface = DefaultStillDecodeBackend(registry).decode(_request(source), _Token())

    assert surface.backend == "qt"
    assert surface.fallback == "imageio_to_qt"


def test_platform_registry_keeps_cancellation_terminal() -> None:
    class _CancelledNative:
        def decode(self, request, cancellation):
            del request, cancellation
            raise DecodeCancelledError("cancelled")

    registry = StillDecodeBackendRegistry(
        platform="win32",
        windows_backend=_CancelledNative(),
    )

    with pytest.raises(DecodeCancelledError):
        DefaultStillDecodeBackend(registry).decode(
            _request(Path("photo.heic")),
            _Token(),
        )


@pytest.mark.parametrize(
    "suffix",
    [".jpg", ".jpeg", ".jpe", ".jfif", ".JPG"],
)
def test_windows_jpegs_bypass_wic(
    tmp_path: Path,
    suffix: str,
) -> None:
    source = tmp_path / f"photo{suffix}"
    image = QImage(64, 32, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)
    format_name = "JPEG"
    if not image.save(str(source), format_name):
        pytest.skip(f"Qt test runtime does not provide the {format_name} writer")

    crashing_wic = MagicMock()
    crashing_wic.decode.side_effect = AssertionError(
        "common still format entered WIC"
    )

    registry = StillDecodeBackendRegistry(
        platform="win32",
        windows_backend=crashing_wic,
    )

    surface = DefaultStillDecodeBackend(registry).decode(_request(source), _Token())

    crashing_wic.decode.assert_not_called()
    assert surface.backend == "qt"
    assert surface.fallback is None


@pytest.mark.parametrize(
    "suffix",
    [".png", ".webp", ".tif", ".heic", ".heif", ".jxr"],
)
def test_windows_non_jpeg_formats_keep_wic_fallback(suffix: str) -> None:
    class _MarkerWic:
        def decode(self, request, cancellation):
            del request, cancellation
            raise DecodeCancelledError("wic selected")

    registry = StillDecodeBackendRegistry(
        platform="win32",
        windows_backend=_MarkerWic(),
    )

    with pytest.raises(DecodeCancelledError, match="wic selected"):
        DefaultStillDecodeBackend(registry).decode(
            _request(Path(f"photo{suffix}")),
            _Token(),
        )


def test_windows_wic_orientation_mapping_covers_exif_transforms() -> None:
    from iPhoto.gui.detail_decode_windows import _orientation_transform

    assert [_orientation_transform(value) for value in range(1, 9)] == [
        0,
        8,
        2,
        10,
        9,
        1,
        11,
        3,
    ]


@pytest.mark.parametrize(
    ("frame_size", "display_size", "orientation", "expected"),
    [
        ((3024, 4032), (3024, 4032), 6, True),
        ((4032, 3024), (3024, 4032), 6, False),
        ((3024, 4032), (3024, 4032), 3, False),
        ((1024, 1024), (1024, 1024), 6, False),
        ((0, 0), (3024, 4032), 6, False),
    ],
)
def test_windows_wic_preoriented_frame_detection_is_strict(
    frame_size: tuple[int, int],
    display_size: tuple[int, int],
    orientation: int,
    expected: bool,
) -> None:
    from iPhoto.gui.detail_decode_windows import (
        _wic_frame_is_already_display_oriented,
    )

    assert (
        _wic_frame_is_already_display_oriented(
            frame_size,
            display_size,
            orientation,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("frame_sizes", "already_oriented"),
    [
        (((3024, 4032), (3024, 4032)), True),
        (((4032, 3024), (3024, 4032)), False),
    ],
    ids=("heic-preoriented", "raw-coded-geometry"),
)
# LIVE PHOTO REGRESSION GUARD — DO NOT DELETE OR WEAKEN THIS TEST.
# It locks both Windows codec behaviours: HEIC frames that WIC already
# presents upright must not be rotated twice, while coded-geometry frames must
# still receive their EXIF transform.  The paired motion video is already
# correct and must not be used to compensate for a wrong still orientation.
def test_windows_wic_decode_avoids_only_proven_double_orientation(
    mocker,
    frame_sizes: tuple[tuple[int, int], tuple[int, int]],
    already_oriented: bool,
) -> None:
    import ctypes

    import iPhoto.gui.detail_decode_windows as wic

    source = Path("C:/library/IMG_3684.HEIC")
    request = DetailRenderRequest(
        generation=1,
        asset_id="live-still",
        source_identity=AssetSourceIdentity.create(
            source,
            size_bytes=100,
            source_mtime_ns=1,
            width=3024,
            height=4032,
            orientation=6,
        ),
        viewport_physical_size=(768, 1024),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
        decode_level=1024,
    )
    apartment = SimpleNamespace(close=MagicMock())
    factory = ctypes.c_void_p(1)
    decoder = ctypes.c_void_p(2)
    frame = ctypes.c_void_p(3)
    oriented = ctypes.c_void_p(4)
    scaler = ctypes.c_void_p(5)
    converter = ctypes.c_void_p(6)
    image = QImage(768, 1024, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)
    mocker.patch.object(wic._ComApartment, "enter", return_value=apartment)
    mocker.patch.object(wic, "_create_factory", return_value=factory)
    mocker.patch.object(wic, "_create_decoder", return_value=decoder)
    mocker.patch.object(wic, "_first_frame", return_value=frame)
    mocker.patch.object(wic, "_source_size", side_effect=frame_sizes)
    apply_orientation = mocker.patch.object(
        wic,
        "_apply_orientation",
        return_value=oriented,
    )
    scale_source = mocker.patch.object(wic, "_scale_source", return_value=scaler)
    mocker.patch.object(wic, "_frame_color_context", return_value=ctypes.c_void_p())
    mocker.patch.object(wic, "_convert_rgba", return_value=converter)
    mocker.patch.object(wic, "_copy_rgba", return_value=image)
    mocker.patch.object(wic, "_release")

    surface = wic.WindowsWicStillDecodeBackend().decode(request, _Token())

    if already_oriented:
        apply_orientation.assert_not_called()
        expected_source = frame
    else:
        apply_orientation.assert_called_once_with(factory, frame, 6)
        expected_source = oriented
    scale_source.assert_called_once_with(factory, expected_source, 768, 1024)
    assert surface.decoded_size == (768, 1024)
    assert surface.orientation_applied is True


def test_windows_wic_hresult_is_a_fixed_signed_32_bit_value() -> None:
    from ctypes import sizeof

    from iPhoto.gui.detail_decode_windows import _HRESULT, _failed

    assert sizeof(_HRESULT) == 4
    assert not _failed(0)
    assert _failed(0x80004005)


def test_windows_wic_uses_embedded_profile_for_srgb_output(mocker) -> None:
    import ctypes

    import iPhoto.gui.detail_decode_windows as wic

    apartment = SimpleNamespace(close=MagicMock())
    factory = ctypes.c_void_p(1)
    decoder = ctypes.c_void_p(2)
    frame = ctypes.c_void_p(3)
    scaler = ctypes.c_void_p(4)
    source_color = ctypes.c_void_p(5)
    target_color = ctypes.c_void_p(6)
    transform = ctypes.c_void_p(7)
    image = QImage(800, 600, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)
    mocker.patch.object(wic._ComApartment, "enter", return_value=apartment)
    mocker.patch.object(wic, "_create_factory", return_value=factory)
    mocker.patch.object(wic, "_create_decoder", return_value=decoder)
    mocker.patch.object(wic, "_first_frame", return_value=frame)
    mocker.patch.object(wic, "_source_size", return_value=(4000, 3000))
    mocker.patch.object(wic, "_apply_orientation", return_value=ctypes.c_void_p())
    mocker.patch.object(wic, "_scale_source", return_value=scaler)
    mocker.patch.object(wic, "_frame_color_context", return_value=source_color)
    mocker.patch.object(wic, "_create_srgb_color_context", return_value=target_color)
    transform_to_srgb = mocker.patch.object(wic, "_transform_to_srgb", return_value=transform)
    convert_rgba = mocker.patch.object(wic, "_convert_rgba")
    copy_rgba = mocker.patch.object(wic, "_copy_rgba", return_value=image)
    mocker.patch.object(wic, "_release")

    surface = wic.WindowsWicStillDecodeBackend().decode(
        _request(Path("profiled.jpg"), level=1024),
        _Token(),
    )

    transform_to_srgb.assert_called_once_with(
        factory,
        scaler,
        source_color,
        target_color,
    )
    convert_rgba.assert_not_called()
    copy_rgba.assert_called_once_with(transform, 1024, 768)
    assert surface.image.colorSpace() == image.colorSpace()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows WIC")
def test_windows_wic_backend_decodes_detached_alpha_surface(tmp_path: Path) -> None:
    from iPhoto.gui.detail_decode_windows import create_windows_wic_backend

    source = tmp_path / "alpha.png"
    image = QImage(64, 32, QImage.Format.Format_RGBA8888)
    image.fill(0x80112233)
    assert image.save(str(source), "PNG")
    backend = create_windows_wic_backend()
    assert backend is not None

    surface = backend.decode(_request(source, level=64), _Token())

    assert surface.backend == "wic"
    assert surface.decoded_size == (64, 32)
    assert surface.image.format() == QImage.Format.Format_RGBA8888
    assert surface.image.hasAlphaChannel()


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


def test_raw_embedded_jpeg_is_scaled_during_decode(tmp_path: Path) -> None:
    source = tmp_path / "photo.nef"
    source.write_bytes(b"raw")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.extract_thumb.return_value = SimpleNamespace(
        format="jpeg",
        data=b"embedded-jpeg",
    )
    rawpy = SimpleNamespace(
        ThumbFormat=SimpleNamespace(JPEG="jpeg", BITMAP="bitmap"),
        imread=MagicMock(return_value=raw),
    )
    scaled_sizes: list[QSize] = []

    class _EmbeddedReader:
        def __init__(self, *_args) -> None:
            self._scaled = QSize(1600, 1200)

        def setAutoTransform(self, _enabled: bool) -> None:
            pass

        def size(self) -> QSize:
            return QSize(1600, 1200)

        def setScaledSize(self, size: QSize) -> None:
            self._scaled = QSize(size)
            scaled_sizes.append(QSize(size))

        def read(self) -> QImage:
            return QImage(
                self._scaled.width(),
                self._scaled.height(),
                QImage.Format.Format_RGB888,
            )

    with patch(
        "iPhoto.gui.detail_decode_backend._import_rawpy",
        return_value=rawpy,
    ), patch(
        "iPhoto.gui.detail_decode_backend.QImageReader",
        _EmbeddedReader,
    ):
        surface = RawStillDecodeBackend().decode(_request(source), _Token())

    assert scaled_sizes == [QSize(1024, 768)]
    assert surface.decoded_size == (1024, 768)
    raw.postprocess.assert_not_called()


def test_raw_backend_falls_back_when_embedded_jpeg_decode_fails(tmp_path: Path) -> None:
    source = tmp_path / "photo.nef"
    source.write_bytes(b"raw")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.sizes = SimpleNamespace(iwidth=3200, iheight=2400, flip=0)
    raw.extract_thumb.return_value = SimpleNamespace(
        format="jpeg",
        data=b"jpeg-with-readable-header-and-broken-pixels",
    )
    raw.postprocess.return_value = np.zeros((1200, 1600, 3), dtype=np.uint8)
    rawpy = SimpleNamespace(
        ThumbFormat=SimpleNamespace(JPEG="jpeg", BITMAP="bitmap"),
        imread=MagicMock(return_value=raw),
    )

    class _BrokenEmbeddedReader:
        def __init__(self, *_args) -> None:
            pass

        def setAutoTransform(self, _enabled: bool) -> None:
            pass

        def size(self) -> QSize:
            return QSize(1600, 1200)

        def setScaledSize(self, _size: QSize) -> None:
            pass

        def read(self) -> QImage:
            return QImage()

    with patch(
        "iPhoto.gui.detail_decode_backend._import_rawpy",
        return_value=rawpy,
    ), patch(
        "iPhoto.gui.detail_decode_backend.QImageReader",
        _BrokenEmbeddedReader,
    ):
        surface = RawStillDecodeBackend().decode(_request(source), _Token())

    assert surface.fallback == "half"
    assert surface.decoded_size == (1024, 768)
    assert [call.kwargs["half_size"] for call in raw.postprocess.call_args_list] == [True]


def test_raw_embedded_scaled_decode_accounts_for_orientation() -> None:
    rawpy = SimpleNamespace(
        ThumbFormat=SimpleNamespace(JPEG="jpeg", BITMAP="bitmap"),
    )
    thumb = SimpleNamespace(format="jpeg", data=b"embedded-jpeg")
    scaled_sizes: list[QSize] = []

    class _RotatedReader:
        def __init__(self, *_args) -> None:
            pass

        def setAutoTransform(self, _enabled: bool) -> None:
            pass

        def size(self) -> QSize:
            return QSize(1600, 1200)

        def setScaledSize(self, size: QSize) -> None:
            scaled_sizes.append(QSize(size))

        def read(self) -> QImage:
            return QImage(768, 1024, QImage.Format.Format_RGB888)

    with patch("iPhoto.gui.detail_decode_backend.QImageReader", _RotatedReader):
        image = _qimage_from_raw_thumb(
            thumb,
            rawpy,
            QSize(768, 1024),
            orientation=6,
        )

    assert scaled_sizes == [QSize(1024, 768)]
    assert image.size() == QSize(768, 1024)


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


def test_raw_backend_skips_half_when_predicted_size_is_insufficient(tmp_path: Path) -> None:
    source = tmp_path / "photo.nef"
    source.write_bytes(b"raw")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.sizes = SimpleNamespace(iwidth=1600, iheight=1200, flip=0)
    raw.extract_thumb.return_value = SimpleNamespace(
        format="bitmap",
        data=np.zeros((100, 100, 3), dtype=np.uint8),
    )
    raw.postprocess.return_value = np.zeros((1200, 1600, 3), dtype=np.uint8)
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
    assert [call.kwargs["half_size"] for call in raw.postprocess.call_args_list] == [False]
    assert surface.decoded_size == (1024, 768)


def test_raw_backend_uses_adequate_half_size_demosaic(tmp_path: Path) -> None:
    source = tmp_path / "photo.cr3"
    source.write_bytes(b"raw")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.sizes = SimpleNamespace(iwidth=3200, iheight=2400, flip=0)
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


def test_raw_array_bridge_is_detached_and_does_not_encode_png() -> None:
    array = np.zeros((2, 3, 3), dtype=np.uint8)
    array[0, 0] = (12, 34, 56)

    image = _qimage_from_array(array)
    del array
    gc.collect()

    assert image.size() == QSize(3, 2)
    assert image.pixelColor(0, 0).getRgb()[:3] == (12, 34, 56)


def test_probe_raw_source_identity_repairs_geometry_and_orientation(tmp_path: Path) -> None:
    source = tmp_path / "legacy.nef"
    source.write_bytes(b"raw")
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.__exit__.return_value = False
    raw.sizes = SimpleNamespace(iwidth=6000, iheight=4000, flip=6)
    rawpy = SimpleNamespace(imread=MagicMock(return_value=raw))
    identity = AssetSourceIdentity.create(
        source,
        size_bytes=123,
        source_mtime_ns=456,
        width=0,
        height=0,
    )

    with patch("iPhoto.gui.detail_decode_backend._import_rawpy", return_value=rawpy):
        repaired = probe_raw_source_identity(identity)

    assert (repaired.width, repaired.height, repaired.orientation) == (4000, 6000, 6)
    assert repaired.revision == identity.revision


def test_raw_backend_reports_corrupt_source_as_decode_failure(tmp_path: Path) -> None:
    source = tmp_path / "broken.nef"
    source.write_bytes(b"broken")
    rawpy = SimpleNamespace(imread=MagicMock(side_effect=RuntimeError("corrupt raw")))

    with patch("iPhoto.gui.detail_decode_backend._import_rawpy", return_value=rawpy):
        with pytest.raises(RuntimeError, match="corrupt raw"):
            RawStillDecodeBackend().decode(_request(source), _Token())
