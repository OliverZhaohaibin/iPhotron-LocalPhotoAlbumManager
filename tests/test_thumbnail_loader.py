import hashlib
import os
import time
from pathlib import Path

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
from src.iPhoto.gui.ui.tasks.thumbnail_loader import ThumbnailLoader
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
    digest = hashlib.blake2b("IMG_0001.JPG".encode("utf-8"), digest_size=20).hexdigest()
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
    ready_spy = QSignalSpy(loader.ready)
    cache_written_spy = QSignalSpy(loader.cache_written)
    validation_spy = QSignalSpy(loader._validation_success)

    loader.request("IMG_VALID.JPG", image_path, QSize(512, 512), is_image=True)
    deadline = time.monotonic() + 4.0
    # Wait for validation signal, but we might not get a ready signal since cache is valid
    while time.monotonic() < deadline and validation_spy.count() < 1:
        qapp.processEvents()
        time.sleep(0.05)
    
    # Validation success should be emitted when cache is still valid
    assert validation_spy.count() >= 1
    # Cache should NOT be written again since it's still valid
    assert cache_written_spy.count() == 0
    # Ready signal may or may not be emitted depending on implementation
    # but the key is that validation succeeded without re-rendering
