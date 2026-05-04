import json
import os
from pathlib import Path
from unittest.mock import patch

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

from iPhoto.library.runtime_controller import LibraryRuntimeController


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


class _QueryService:
    def __init__(self, repo) -> None:
        self._repo = repo

    def read_geotagged_rows(self):
        return self._repo.read_geotagged()


class _LocationService:
    def __init__(self, assets: list) -> None:
        self.assets = assets
        self.calls = 0
        self.invalidations = 0

    def list_geotagged_assets(self):
        self.calls += 1
        return list(self.assets)

    def invalidate_cache(self) -> None:
        self.invalidations += 1


def test_geotagged_assets_use_classifier(tmp_path: Path, qapp: QApplication) -> None:
    """Ensure GPS-enabled assets are classified even if flags are missing."""

    root = tmp_path / "Library"
    album = root / "Album"
    asset_path = album / "photo.jpg"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"fake-image")
    _write_album_manifest(album)

    row = {
        "rel": "Album/photo.jpg",
        "gps": {"lat": 10.0, "lon": 20.0},
        "mime": "image/jpeg",
        "id": "asset-1",
        "parent_album_path": "Album",
    }

    # Insert data through the repository API (the real data path)
    from iPhoto.cache.index_store import get_global_repository
    repo = get_global_repository(root)
    repo.write_rows([row])

    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    assets = manager.get_geotagged_assets()
    assert len(assets) == 1
    asset = assets[0]
    assert asset.is_image is True
    assert asset.is_video is False


def test_geotagged_assets_reuse_cached_rows_until_library_changes(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Repeated Location opens should not reread geotagged rows every time."""

    del qapp
    root = tmp_path / "Library"
    album = root / "Album"
    asset_path = album / "photo.jpg"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"fake-image")
    _write_album_manifest(album)

    row = {
        "rel": "Album/photo.jpg",
        "gps": {"lat": 10.0, "lon": 20.0},
        "mime": "image/jpeg",
        "id": "asset-1",
        "parent_album_path": "Album",
    }

    class _Repo:
        def __init__(self) -> None:
            self.calls = 0

        def read_geotagged(self):
            self.calls += 1
            return [row]

    repo = _Repo()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    manager.bind_asset_query_service(_QueryService(repo))

    first = manager.get_geotagged_assets()
    second = manager.get_geotagged_assets()

    assert repo.calls == 1
    assert first == second


def test_geotagged_assets_delegate_to_bound_location_service(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    del qapp
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    asset = object()
    service = _LocationService([asset])
    manager.bind_location_service(service)  # type: ignore[arg-type]

    assert manager.get_geotagged_assets() == [asset]
    assert service.calls == 1

    manager.invalidate_geotagged_assets_cache()

    assert service.invalidations == 1


def test_scan_chunk_invalidates_geotagged_cache(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    album = root / "Album"
    album.mkdir(parents=True, exist_ok=True)
    _write_album_manifest(album)

    row = {
        "rel": "Album/photo.jpg",
        "gps": {"lat": 10.0, "lon": 20.0},
        "mime": "image/jpeg",
        "id": "asset-1",
        "parent_album_path": "Album",
    }

    class _Repo:
        def __init__(self) -> None:
            self.calls = 0

        def read_geotagged(self):
            self.calls += 1
            return [row]

    repo = _Repo()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    manager.bind_asset_query_service(_QueryService(repo))

    with (
        patch("iPhoto.library.geo_aggregator.resolve_location_name", return_value=None),
    ):
        manager.get_geotagged_assets()
        manager._on_scan_chunk(root, [{"rel": "Album/new.jpg", "id": "asset-2"}])
        manager.get_geotagged_assets()

    assert repo.calls == 2


def test_scan_finished_invalidates_geotagged_cache(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    album = root / "Album"
    album.mkdir(parents=True, exist_ok=True)
    _write_album_manifest(album)

    row = {
        "rel": "Album/photo.jpg",
        "gps": {"lat": 10.0, "lon": 20.0},
        "mime": "image/jpeg",
        "id": "asset-1",
        "parent_album_path": "Album",
    }

    class _Repo:
        def __init__(self) -> None:
            self.calls = 0

        def read_geotagged(self):
            self.calls += 1
            return [row]

    repo = _Repo()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    manager.bind_asset_query_service(_QueryService(repo))

    with (
        patch("iPhoto.library.geo_aggregator.resolve_location_name", return_value=None),
        patch("iPhoto.library.scan_coordinator.LOGGER.warning"),
    ):
        manager.get_geotagged_assets()
        manager._on_scan_finished(root, [row])
        manager.get_geotagged_assets()

    assert repo.calls == 2
