from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for facade tests",
    exc_type=ImportError,
)

from iPhoto.gui.facade import AppFacade


class FakeScanService:
    def __init__(self, asset_count: int = 1) -> None:
        self.asset_count = asset_count
        self.prepared: list[dict] = []

    def prepare_album_open(self, root: Path, **kwargs):
        self.prepared.append({"root": root, **kwargs})
        return SimpleNamespace(asset_count=self.asset_count)


class DummyLibrary:
    def __init__(self, root: Path, scan_service: FakeScanService) -> None:
        self._root = root
        self.scan_service = scan_service
        self.started: list[tuple[Path, list[str], list[str]]] = []

    def root(self) -> Path:
        return self._root

    def is_scanning_path(self, _path: Path) -> bool:
        return False

    def start_scanning(self, root: Path, include, exclude) -> None:
        self.started.append((root, list(include), list(exclude)))


def test_facade_open_album_uses_session_scan_service(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    scan_service = FakeScanService(asset_count=3)
    library = DummyLibrary(library_root, scan_service)
    facade = AppFacade()
    facade._library_manager = library

    album = facade.open_album(album_root)

    assert album is not None
    assert album.root == album_root
    assert scan_service.prepared == [
        {
            "root": album_root,
            "autoscan": False,
            "hydrate_index": False,
            "sync_manifest_favorites": False,
        }
    ]
    assert library.started == []
