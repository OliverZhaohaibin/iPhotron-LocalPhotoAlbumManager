
import pytest
from src.iPhoto.io.scanner import scan_album
from src.iPhoto.config import DEFAULT_INCLUDE, DEFAULT_EXCLUDE

@pytest.fixture
def scan_test_dir(tmp_path):
    """Creates a temporary directory with various file extensions.

    We use different base names for uppercase and lowercase variants to ensure
    they exist as distinct files even on case-insensitive filesystems (Windows/macOS).
    """
    # Create files
    files = [
        "image_lower.heif",
        "image_upper.HEIF",
        "image_lower.heic",
        "image_upper.HEIC",
        "image_lower.jpg",
        "image_upper.JPG",
        "video_lower.mov",
        "video_upper.MOV",
        "image.heifs",
        "image.heicf"
    ]

    for f in files:
        (tmp_path / f).touch()

    return tmp_path, files

def test_scanner_supports_heif_and_case_sensitivity(scan_test_dir):
    root, files = scan_test_dir

    # Run scan
    rows = list(scan_album(root, DEFAULT_INCLUDE, DEFAULT_EXCLUDE))
    found_rels = {row["rel"] for row in rows}

    # Check all files are found
    missing = []
    for f in files:
        if f not in found_rels:
            missing.append(f)

    assert not missing, f"The following files were not indexed: {missing}"
