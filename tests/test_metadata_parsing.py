from iPhoto.io.metadata import _coerce_fractional
from pathlib import Path
from iPhoto.io.metadata import read_image_meta_with_exiftool
import pytest

def test_coerce_fractional_duplicate_sum():
    """Test that _coerce_fractional does not sum duplicate values in the string."""
    # Scenario: Metadata contains both fractional and decimal representation
    # e.g. "1/125s (0.008)" which some tools might output
    value = "1/125s (0.008)"

    # Expected: 0.008 (1/125)
    # Before fix (bug): 0.008 + 0.008 = 0.016

    result = _coerce_fractional(value)
    assert result == pytest.approx(0.008, rel=1e-3)

def test_coerce_fractional_standard_cases():
    """Ensure standard cases still work correctly."""
    assert _coerce_fractional("1/30") == pytest.approx(1/30)
    assert _coerce_fractional("30") == 30.0
    assert _coerce_fractional("2.8") == 2.8
    assert _coerce_fractional("0") == 0.0
    assert _coerce_fractional(None) is None
    assert _coerce_fractional("") is None

def test_coerce_fractional_negative():
    """Ensure negative values are handled."""
    assert _coerce_fractional("-1") == -1.0
    assert _coerce_fractional("-0.5") == -0.5


# ── Image lens extraction B-level fallbacks ───────────────────────────────────


def test_image_lens_exif_ifd_lens_info_used_as_spec(tmp_path: Path) -> None:
    """Fujifilm-style: ExifIFD:LensInfo is used when no LensModel is present."""
    exif_payload = {
        "ExifIFD": {"LensInfo": "23mm f/2"},
    }
    info = read_image_meta_with_exiftool(tmp_path / "img.jpg", exif_payload)
    assert info["lens"] == "23mm f/2"


def test_image_lens_exif_lens_specification_fallback(tmp_path: Path) -> None:
    """EXIF:LensSpecification is used as B-level fallback."""
    exif_payload = {
        "EXIF": {"LensSpecification": "18-55mm f/3.5-5.6"},
    }
    info = read_image_meta_with_exiftool(tmp_path / "img.jpg", exif_payload)
    assert info["lens"] == "18-55mm f/3.5-5.6"


def test_image_lens_name_beats_spec_string(tmp_path: Path) -> None:
    """ExifIFD:LensModel (A-level) wins over ExifIFD:LensInfo (B-level)."""
    exif_payload = {
        "ExifIFD": {
            "LensModel": "XF23mmF2 R WR",
            "LensInfo": "23mm f/2",
        },
    }
    info = read_image_meta_with_exiftool(tmp_path / "img.jpg", exif_payload)
    assert info["lens"] == "XF23mmF2 R WR"


def test_image_fujifilm_xt4_style(tmp_path: Path) -> None:
    """End-to-end: Fujifilm X-T4 ExifTool payload is fully parsed."""
    exif_payload = {
        "IFD0": {"Make": "FUJIFILM", "Model": "X-T4"},
        "ExifIFD": {
            "ExposureTime": "1/2700",
            "FNumber": 5.6,
            "ISO": 640,
            "LensInfo": "23mm f/2",
            "FocalLength": "23",
        },
    }
    info = read_image_meta_with_exiftool(tmp_path / "img.jpg", exif_payload)

    assert info["make"] == "FUJIFILM"
    assert info["model"] == "X-T4"
    assert info["lens"] == "23mm f/2"
    assert info["iso"] == 640
    assert info["f_number"] == pytest.approx(5.6)
    assert info["focal_length"] == pytest.approx(23.0)

