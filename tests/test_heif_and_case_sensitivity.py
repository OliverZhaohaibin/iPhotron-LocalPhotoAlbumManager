
import pytest
from iPhotos.src.iPhoto.io.scanner import scan_album
from iPhotos.src.iPhoto.config import DEFAULT_INCLUDE, DEFAULT_EXCLUDE

@pytest.fixture
def scan_test_dir(tmp_path):
    """Creates a temporary directory with various file extensions."""
    # Create files
    files = [
        "image.heif",
        "IMAGE.HEIF",
        "image.heic",
        "IMAGE.HEIC",
        "image.jpg",
        "IMAGE.JPG",
        "video.mov",
        "VIDEO.MOV",
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
