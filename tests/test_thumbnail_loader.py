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
from src.iPhoto.gui.ui.tasks.thumbnail_loader import ThumbnailLoader, generate_cache_path, safe_unlink
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


def test_generate_cache_path_basic(tmp_path: Path) -> None:
    """Test basic cache path generation."""
    album_root = tmp_path / "album"
    rel = "photos/IMG_0001.JPG"
    abs_path = album_root / rel
    size = QSize(512, 512)
    stamp = 1234567890
    
    result = generate_cache_path(album_root, abs_path, size, stamp)
    
    # Verify the path structure
    assert result.parent.parent.name == WORK_DIR_NAME
    assert result.parent.name == "thumbs"
    assert result.suffix == ".png"
    
    # Verify filename includes stamp and size
    filename = result.name
    assert f"{stamp}_" in filename
    assert "512x512.png" in filename


def test_generate_cache_path_hash_consistency(tmp_path: Path) -> None:
    """Test that the hash is consistent for the same relative path."""
    album_root = tmp_path / "album"
    rel = "photos/IMG_0001.JPG"
    abs_path = album_root / rel
    size = QSize(512, 512)
    stamp = 1234567890
    
    result1 = generate_cache_path(album_root, abs_path, size, stamp)
    result2 = generate_cache_path(album_root, abs_path, size, stamp)
    
    # Same inputs should produce identical paths
    assert result1 == result2
    
    # Verify the hash is blake2b (20 bytes = 40 hex chars)
    filename = result1.name
    hash_part = filename.split("_")[0]
    assert len(hash_part) == 40  # blake2b with digest_size=20 produces 40 hex chars


def test_generate_cache_path_different_sizes(tmp_path: Path) -> None:
    """Test that different sizes produce different cache paths."""
    album_root = tmp_path / "album"
    rel = "photos/IMG_0001.JPG"
    abs_path = album_root / rel
    stamp = 1234567890
    
    result_512 = generate_cache_path(album_root, abs_path, QSize(512, 512), stamp)
    result_256 = generate_cache_path(album_root, abs_path, QSize(256, 256), stamp)
    
    # Different sizes should produce different filenames
    assert result_512 != result_256
    assert "512x512.png" in result_512.name
    assert "256x256.png" in result_256.name


def test_thumbnail_loader_fixed_size(tmp_path: Path, qapp: QApplication) -> None:
    """Test that ThumbnailLoader enforces 512x512 regardless of requested size."""
    image_path = tmp_path / "IMG_FIXED.JPG"
    _create_image(image_path)
    loader = ThumbnailLoader()
    loader.reset_for_album(tmp_path)

    spy = QSignalSpy(loader.ready)
    # Request a small size
    loader.request("IMG_FIXED.JPG", image_path, QSize(100, 100), is_image=True)

    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and spy.count() < 1:
        qapp.processEvents()
        time.sleep(0.05)
    assert spy.count() >= 1

    thumbs_dir = tmp_path / WORK_DIR_NAME / "thumbs"
    files = list(thumbs_dir.iterdir())
    assert len(files) == 1

    # The file generated should imply 512x512 in its name
    assert "512x512.png" in files[0].name
    assert "100x100" not in files[0].name


def test_thumbnail_loader_lru_eviction(tmp_path: Path, qapp: QApplication) -> None:
    """Test that the LRU cache evicts old items when limit is reached."""
    loader = ThumbnailLoader()
    loader.reset_for_album(tmp_path)

    # Lower the limit for testing
    loader._max_memory_items = 2

    # Helper to create and load images
    def load_image(name: str):
        path = tmp_path / name
        _create_image(path)
        spy = QSignalSpy(loader.ready)
        loader.request(name, path, QSize(512, 512), is_image=True)
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and spy.count() < 1:
            qapp.processEvents()
            time.sleep(0.05)
        return spy.count() >= 1

    # Load 3 images
    assert load_image("IMG_1.JPG")
    assert load_image("IMG_2.JPG")
    assert load_image("IMG_3.JPG")

    # Wait for processing to potentially stabilize
    qapp.processEvents()

    # Check memory cache size
    assert len(loader._memory) == 2

    # Verify contents of cache: IMG_1 should be evicted because it is the least recently used (LRU); IMG_2 and IMG_3 should remain
    # Keys structure: (album_root_str, rel, width, height)
    keys = list(loader._memory.keys())
    rels = [k[1] for k in keys]

    assert "IMG_1.JPG" not in rels
    assert "IMG_2.JPG" in rels
    assert "IMG_3.JPG" in rels

    # Access IMG_2 again to make it most recently used
    loader.request("IMG_2.JPG", tmp_path / "IMG_2.JPG", QSize(512, 512), is_image=True)

    # Load a 4th image
    assert load_image("IMG_4.JPG")

    # Now IMG_3 should be evicted (least recently used), IMG_2 and IMG_4 remain
    keys = list(loader._memory.keys())
    rels = [k[1] for k in keys]

    assert "IMG_3.JPG" not in rels
    assert "IMG_2.JPG" in rels
    assert "IMG_4.JPG" in rels


def test_generate_cache_path_different_stamps(tmp_path: Path) -> None:
    """Test that different timestamps produce different cache paths."""
    album_root = tmp_path / "album"
    rel = "photos/IMG_0001.JPG"
    abs_path = album_root / rel
    size = QSize(512, 512)
    
    result_old = generate_cache_path(album_root, abs_path, size, 1234567890)
    result_new = generate_cache_path(album_root, abs_path, size, 9876543210)
    
    # Different timestamps should produce different filenames
    assert result_old != result_new
    assert "_1234567890_" in result_old.name
    assert "_9876543210_" in result_new.name


def test_generate_cache_path_different_rel_paths(tmp_path: Path) -> None:
    """Test that different relative paths produce different cache paths."""
    album_root = tmp_path / "album"
    size = QSize(512, 512)
    stamp = 1234567890
    
    result1 = generate_cache_path(album_root, album_root / "photos/IMG_0001.JPG", size, stamp)
    result2 = generate_cache_path(album_root, album_root / "photos/IMG_0002.JPG", size, stamp)
    
    # Different relative paths should produce different hash prefixes
    assert result1 != result2
    hash1 = result1.name.split("_")[0]
    hash2 = result2.name.split("_")[0]
    assert hash1 != hash2


def test_generate_cache_path_hash_algorithm(tmp_path: Path) -> None:
    """Test that the hash uses blake2b algorithm with correct digest size."""
    album_root = tmp_path / "album"
    rel = "photos/IMG_0001.JPG"
    abs_path = album_root / rel
    size = QSize(512, 512)
    stamp = 1234567890
    
    result = generate_cache_path(album_root, abs_path, size, stamp)
    
    # Calculate expected hash
    expected_hash = hashlib.blake2b(str(abs_path.resolve()).encode("utf-8"), digest_size=20).hexdigest()
    
    # Verify the filename starts with the expected hash
    filename = result.name
    assert filename.startswith(expected_hash)


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
    digest = hashlib.blake2b(str(image_path.resolve()).encode("utf-8"), digest_size=20).hexdigest()
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


def test_thumbnail_loader_cache_validation(tmp_path: Path, qapp: QApplication) -> None:
    """Test that _report_valid is called when cached thumbnail is still current."""
    image_path = tmp_path / "IMG_VALID.JPG"
    _create_image(image_path)
    loader = ThumbnailLoader()
    loader.reset_for_album(tmp_path)

    # Initial request - generates thumbnail and caches it
    ready_spy = QSignalSpy(loader.ready)
    cache_written_spy = QSignalSpy(loader.cache_written)
    validation_spy = QSignalSpy(loader._validation_success)

    loader.request("IMG_VALID.JPG", image_path, QSize(512, 512), is_image=True)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and ready_spy.count() < 1:
        qapp.processEvents()
        time.sleep(0.05)

    assert ready_spy.count() >= 1
    assert cache_written_spy.count() >= 1

    # Second request - file hasn't changed, cache should be valid
    # Should emit _validation_success and NOT emit cache_written
    cache_written_spy = QSignalSpy(loader.cache_written)
    validation_spy = QSignalSpy(loader._validation_success)

    # Request should return cached pixmap immediately
    cached_pixmap = loader.request("IMG_VALID.JPG", image_path, QSize(512, 512), is_image=True)
    assert cached_pixmap is not None, "Cached pixmap should be returned immediately"

    # Wait for validation signal to be emitted from background job
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and validation_spy.count() < 1:
        qapp.processEvents()
        time.sleep(0.05)

    # Validation success should be emitted when cache is still valid
    assert validation_spy.count() >= 1
    # Cache should NOT be written again since it's still valid
    assert cache_written_spy.count() == 0
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
