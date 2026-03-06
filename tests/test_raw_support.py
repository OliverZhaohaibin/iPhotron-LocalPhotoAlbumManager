"""Tests for RAW image processing and full-chain RAW support."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iPhoto.core.raw_processor import RAW_EXTENSIONS, is_raw_extension, load_raw_to_pil
from iPhoto.media_classifier import (
    ALL_IMAGE_EXTENSIONS,
    IMAGE_EXTENSIONS,
    RAW_EXTENSIONS as MC_RAW_EXTENSIONS,
    classify_media,
    get_media_type,
    MediaType,
)


# ── raw_processor tests ─────────────────────────────────────────────────────

class TestIsRawExtension:
    def test_known_raw_extensions(self) -> None:
        for ext in [".cr2", ".CR2", ".nef", ".NEF", ".arw", ".dng", ".orf", ".rw2"]:
            assert is_raw_extension(ext), f"{ext} should be recognised as RAW"

    def test_non_raw_extensions(self) -> None:
        for ext in [".jpg", ".png", ".heic", ".mp4", ".mov", ""]:
            assert not is_raw_extension(ext), f"{ext} should NOT be RAW"


class TestRawExtensionsSets:
    def test_raw_extensions_are_lowercase(self) -> None:
        for ext in RAW_EXTENSIONS:
            assert ext == ext.lower()
            assert ext.startswith(".")

    def test_no_overlap_with_standard_image(self) -> None:
        overlap = RAW_EXTENSIONS & IMAGE_EXTENSIONS
        assert overlap == set(), f"Overlap between RAW and IMAGE: {overlap}"

    def test_all_image_is_union(self) -> None:
        assert ALL_IMAGE_EXTENSIONS == IMAGE_EXTENSIONS | MC_RAW_EXTENSIONS


class TestLoadRawToPil:
    def test_returns_none_when_rawpy_missing(self, tmp_path: Path) -> None:
        fake = tmp_path / "photo.cr2"
        fake.write_bytes(b"\x00" * 100)
        with patch("iPhoto.core.raw_processor._import_rawpy", return_value=None):
            result = load_raw_to_pil(fake)
        assert result is None

    def test_returns_none_on_decode_failure(self, tmp_path: Path) -> None:
        fake = tmp_path / "corrupt.nef"
        fake.write_bytes(b"not a raw file")
        # rawpy.imread will raise on garbage data
        result = load_raw_to_pil(fake)
        assert result is None

    def test_returns_pil_image_on_success(self, tmp_path: Path) -> None:
        """Verify the happy path using a mocked rawpy."""
        import numpy as np
        from PIL import Image

        fake = tmp_path / "photo.dng"
        fake.write_bytes(b"\x00" * 100)

        mock_rawpy = MagicMock()
        mock_raw = MagicMock()
        mock_raw.__enter__ = MagicMock(return_value=mock_raw)
        mock_raw.__exit__ = MagicMock(return_value=False)
        mock_raw.raw_image = np.zeros((100, 150), dtype=np.uint16)
        mock_raw.postprocess.return_value = np.zeros((50, 75, 3), dtype=np.uint8)
        mock_rawpy.imread.return_value = mock_raw

        with patch("iPhoto.core.raw_processor._import_rawpy", return_value=mock_rawpy):
            result = load_raw_to_pil(fake, half_size=True)

        assert result is not None
        assert isinstance(result, Image.Image)
        assert result.size == (75, 50)

    def test_auto_half_size_for_small_target(self, tmp_path: Path) -> None:
        """When the target is much smaller than the sensor, half_size should be auto-selected."""
        import numpy as np

        fake = tmp_path / "big.cr2"
        fake.write_bytes(b"\x00" * 100)

        mock_rawpy = MagicMock()
        mock_raw = MagicMock()
        mock_raw.__enter__ = MagicMock(return_value=mock_raw)
        mock_raw.__exit__ = MagicMock(return_value=False)
        # Simulate a large sensor
        mock_raw.raw_image = MagicMock()
        mock_raw.raw_image.shape = (4000, 6000)
        mock_raw.postprocess.return_value = np.zeros((2000, 3000, 3), dtype=np.uint8)
        mock_rawpy.imread.return_value = mock_raw

        with patch("iPhoto.core.raw_processor._import_rawpy", return_value=mock_rawpy):
            load_raw_to_pil(fake, half_size=False, target_size=(256, 256))

        # Expect half_size=True was passed because target << sensor size
        call_kwargs = mock_raw.postprocess.call_args[1]
        assert call_kwargs["half_size"] is True


# ── media_classifier tests ───────────────────────────────────────────────────

class TestMediaClassifierRaw:
    def test_raw_classified_as_image(self) -> None:
        for ext in [".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf"]:
            row = {"rel": f"photo{ext}"}
            is_img, is_vid = classify_media(row)
            assert is_img is True, f"{ext} should classify as image"
            assert is_vid is False

    def test_get_media_type_raw(self) -> None:
        for ext in [".cr2", ".nef", ".arw", ".dng"]:
            assert get_media_type(Path(f"photo{ext}")) == MediaType.IMAGE


# ── settings schema tests ───────────────────────────────────────────────────

class TestExportFormatSetting:
    def test_default_export_format(self) -> None:
        from iPhoto.settings.schema import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["ui"]["export_format"] == "jpg"

    def test_schema_accepts_valid_formats(self) -> None:
        from iPhoto.settings.schema import merge_with_defaults
        for fmt in ("jpg", "png", "tiff"):
            data = {"ui": {"export_format": fmt}}
            merged = merge_with_defaults(data)
            assert merged["ui"]["export_format"] == fmt

    def test_schema_rejects_invalid_format(self) -> None:
        from jsonschema import ValidationError
        from iPhoto.settings.schema import merge_with_defaults
        with pytest.raises(ValidationError):
            merge_with_defaults({"ui": {"export_format": "bmp"}})


# ── export pipeline tests ───────────────────────────────────────────────────

class TestExportFormatConstants:
    def test_supported_formats(self) -> None:
        from iPhoto.core.export import EXPORT_FORMATS, DEFAULT_EXPORT_FORMAT
        assert "jpg" in EXPORT_FORMATS
        assert "png" in EXPORT_FORMATS
        assert "tiff" in EXPORT_FORMATS
        assert DEFAULT_EXPORT_FORMAT == "jpg"

    def test_format_tuples_structure(self) -> None:
        from iPhoto.core.export import EXPORT_FORMATS
        for key, (qt_fmt, suffix) in EXPORT_FORMATS.items():
            assert isinstance(qt_fmt, str)
            assert suffix.startswith(".")


@patch("iPhoto.core.export.render_image")
@patch("iPhoto.core.export.image_loader")
@patch("iPhoto.core.export.sidecar")
class TestExportAssetRaw:
    def test_raw_without_sidecar_still_exports(
        self, mock_sidecar, mock_loader, mock_render, tmp_path: Path,
    ) -> None:
        """RAW files should always be rendered even without sidecar edits."""
        from iPhoto.core.export import export_asset

        export_root = tmp_path / "exported"
        library_root = tmp_path
        album = tmp_path / "Album"
        album.mkdir()
        raw_file = album / "photo.cr2"
        raw_file.touch()

        # No sidecar
        mock_sc = MagicMock()
        mock_sc.exists.return_value = False
        mock_sidecar.sidecar_path_for_asset.return_value = mock_sc

        # render_image returns None (no adjustments) but load_qimage succeeds
        mock_render.return_value = None
        mock_qimage = MagicMock()
        mock_loader.load_qimage.return_value = mock_qimage

        result = export_asset(raw_file, export_root, library_root, "png")

        assert result is True
        mock_loader.load_qimage.assert_called_with(raw_file)
        # Saved as PNG
        mock_qimage.save.assert_called_once()
        save_args = mock_qimage.save.call_args
        assert save_args[0][1] == "PNG"

    def test_export_with_tiff_format(
        self, mock_sidecar, mock_loader, mock_render, tmp_path: Path,
    ) -> None:
        from iPhoto.core.export import export_asset

        export_root = tmp_path / "exported"
        library_root = tmp_path
        album = tmp_path / "Album"
        album.mkdir()
        raw_file = album / "photo.nef"
        raw_file.touch()

        mock_sc = MagicMock()
        mock_sc.exists.return_value = True
        mock_sidecar.sidecar_path_for_asset.return_value = mock_sc

        mock_qimage = MagicMock()
        mock_render.return_value = mock_qimage

        result = export_asset(raw_file, export_root, library_root, "tiff")

        assert result is True
        save_args = mock_qimage.save.call_args
        assert save_args[0][1] == "TIFF"
