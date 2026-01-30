import json
import os
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for library tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets not available",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication

from src.iPhoto.cache.index_store import get_global_repository, reset_global_repository
from src.iPhoto.library.manager import LibraryManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    if existing is not None:
        yield existing
        return
    app = QApplication([])
    yield app


def _write_album_manifest(album_path: Path) -> None:
    """Create a minimal album manifest so the directory is recognised."""

    payload = {
        "schema": "iPhoto/album@1",
        "title": album_path.name,
        "filters": {},
    }
    manifest_path = album_path / ".iphoto.album.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")


def test_geotagged_assets_use_classifier(tmp_path: Path, qapp: QApplication) -> None:
    """Ensure GPS-enabled assets are classified even if flags are missing."""

    root = tmp_path / "Library"
    album = root / "Album"
    asset_path = album / "photo.jpg"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"fake-image")
    _write_album_manifest(album)

    reset_global_repository()
    store = get_global_repository(root)
    row = {
        "rel": "Album/photo.jpg",
        "gps": {"lat": 10.0, "lon": 20.0},
        "mime": "image/jpeg",
        "id": "asset-1",
    }
    store.write_rows([row])

    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    try:
        assets = manager.get_geotagged_assets()
        assert len(assets) == 1
        asset = assets[0]
        assert asset.is_image is True
        assert asset.is_video is False
    finally:
        reset_global_repository()
