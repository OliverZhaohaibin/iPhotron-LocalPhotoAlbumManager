import hashlib
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for thumbnail tests", exc_type=ImportError)
pytest.importorskip(
    "PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError
)
pytest.importorskip("PySide6.QtTest", reason="Qt test utilities unavailable", exc_type=ImportError)

from PySide6.QtCore import QSize
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from src.iPhoto.config import WORK_DIR_NAME
from src.iPhoto.gui.ui.tasks.thumbnail_loader import ThumbnailLoader, safe_unlink
from src.iPhoto.io.sidecar import save_adjustments

try:
    from PIL import Image
except Exception as exc:  # pragma: no cover - pillow missing or broken
    pytest.skip(
        f"Pillow unavailable for thumbnail loader tests: {exc}",
        allow_module_level=True,
    )


def _create_image(path: Path) -> None:
    image = Image.new("RGB", (16, 16), color="red")
    image.save(path)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_thumbnail_loader_cache_naming(tmp_path: Path, qapp: QApplication) -> None:
    image_path = tmp_path / "IMG_0001.JPG"
    _create_image(image_path)
    loader = ThumbnailLoader()
    loader.reset_for_album(tmp_path)

    spy = QSignalSpy(loader.ready)
    pixmap = loader.request("IMG_0001.JPG", image_path, QSize(512, 512), is_image=True)
    assert pixmap is None
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and spy.count() < 1:
        qapp.processEvents()
        time.sleep(0.05)
    assert spy.count() >= 1

    thumbs_dir = tmp_path / WORK_DIR_NAME / "thumbs"
    files = list(thumbs_dir.iterdir())
    assert len(files) == 1
    filename = files[0].name
    digest = hashlib.sha1("IMG_0001.JPG".encode("utf-8")).hexdigest()
    assert filename.startswith(f"{digest}_")
    assert filename.endswith("_512x512.png")

    # Changing the modification time should produce a new cache entry and
    # remove the stale one.
    os.utime(image_path, None)
    spy = QSignalSpy(loader.ready)
    loader.request("IMG_0001.JPG", image_path, QSize(512, 512), is_image=True)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and spy.count() < 1:
        qapp.processEvents()
        time.sleep(0.05)
    assert spy.count() >= 1
    files = list(thumbs_dir.iterdir())
    assert len(files) == 1
    assert files[0].name != filename

def test_thumbnail_loader_sidecar_invalidation(tmp_path: Path, qapp: QApplication) -> None:
    image_path = tmp_path / "IMG_SIDE.JPG"
    _create_image(image_path)
    loader = ThumbnailLoader()
    loader.reset_for_album(tmp_path)

    # Initial request
    spy = QSignalSpy(loader.ready)
    loader.request("IMG_SIDE.JPG", image_path, QSize(512, 512), is_image=True)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and spy.count() < 1:
        qapp.processEvents()
        time.sleep(0.05)
    assert spy.count() >= 1

    thumbs_dir = tmp_path / WORK_DIR_NAME / "thumbs"
    files = list(thumbs_dir.iterdir())
    assert len(files) == 1
    original_cache_file = files[0].name

    # Create sidecar with edits - ensure mtime is newer
    save_adjustments(image_path, {"Light_Master": 0.5})
    sidecar_path = image_path.with_suffix(".ipo")
    image_mtime = image_path.stat().st_mtime
    os.utime(sidecar_path, (image_mtime + 5, image_mtime + 5))

    # Request again - should trigger new generation because sidecar is newer
    spy = QSignalSpy(loader.ready)

    loader.request("IMG_SIDE.JPG", image_path, QSize(512, 512), is_image=True)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and spy.count() < 1:
        qapp.processEvents()
        time.sleep(0.05)
    assert spy.count() >= 1

    files = list(thumbs_dir.iterdir())
    assert len(files) == 1
    new_cache_file = files[0].name

    assert new_cache_file != original_cache_file


def test_safe_unlink_successful_deletion(tmp_path: Path) -> None:
    """Test that safe_unlink successfully deletes a file when it exists."""
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("test content")
    assert test_file.exists()
    
    safe_unlink(test_file)
    
    assert not test_file.exists()


def test_safe_unlink_missing_file(tmp_path: Path) -> None:
    """Test that safe_unlink handles missing files gracefully."""
    test_file = tmp_path / "nonexistent_file.txt"
    assert not test_file.exists()
    
    # Should not raise an exception
    safe_unlink(test_file)
    
    assert not test_file.exists()


def test_safe_unlink_permission_error(tmp_path: Path) -> None:
    """Test that safe_unlink renames file with .stale suffix on PermissionError."""
    test_file = tmp_path / "locked_file.txt"
    test_file.write_text("test content")
    assert test_file.exists()
    
    # Mock unlink to raise PermissionError
    with patch.object(Path, "unlink", side_effect=PermissionError("Permission denied")):
        safe_unlink(test_file)
    
    # Original file should not exist (renamed to .stale)
    assert not test_file.exists()
    # A file with .stale suffix should exist
    stale_file = test_file.with_suffix(test_file.suffix + ".stale")
    assert stale_file.exists()
    # Verify the content was preserved
    assert stale_file.read_text() == "test content"


def test_safe_unlink_permission_error_rename_fails(tmp_path: Path) -> None:
    """Test that safe_unlink handles OSError during rename gracefully."""
    test_file = tmp_path / "locked_file2.txt"
    test_file.write_text("test content")
    assert test_file.exists()
    
    # Mock unlink to raise PermissionError and rename to raise OSError
    with patch.object(Path, "unlink", side_effect=PermissionError("Permission denied")):
        with patch.object(Path, "rename", side_effect=OSError("Cannot rename")):
            # Should not raise an exception
            safe_unlink(test_file)
    
    # File should still exist (because we mocked both operations to fail)
    assert test_file.exists()


def test_safe_unlink_oserror_during_unlink(tmp_path: Path) -> None:
    """Test that safe_unlink handles OSError during unlink gracefully."""
    test_file = tmp_path / "error_file.txt"
    test_file.write_text("test content")
    assert test_file.exists()
    
    # Mock unlink to raise a generic OSError (not PermissionError)
    with patch.object(Path, "unlink", side_effect=OSError("Generic OS error")):
        # Should not raise an exception
        safe_unlink(test_file)
    
    # File should still exist (because we mocked unlink to fail)
    assert test_file.exists()
