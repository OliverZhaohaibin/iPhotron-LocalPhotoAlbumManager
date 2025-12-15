import os
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class DummyAlbum:
    def __init__(self, root: Path):
        self.root = root
        self.manifest = {}


class DummyFacade(QObject):
    linksUpdated = Signal(Path)
    assetUpdated = Signal(Path)
    scanChunkReady = Signal(Path, list)
    errorRaised = Signal(str)

    def __init__(self):
        super().__init__()
        self.library_manager = None
        self.current_album = None


def test_dedupe_items_filters_existing(tmp_path: Path, qapp: QApplication) -> None:
    facade = DummyFacade()
    model = AssetListModel(facade)
    model._album_root = tmp_path
    model._state_manager.set_rows([{"rel": "exists.jpg"}])

    deduped = model._dedupe_items(
        [{"rel": "exists.jpg"}, {"rel": "fresh.jpg"}]
    )

    assert len(deduped) == 1
    assert deduped[0]["rel"] == "fresh.jpg"


def test_build_entries_from_scan_rows_normalizes_and_filters(tmp_path: Path, qapp: QApplication) -> None:
    facade = DummyFacade()
    model = AssetListModel(facade)

    album_root = tmp_path / "Library"
    album_root.mkdir()
    media_dir = album_root / "sub"
    media_dir.mkdir()
    media_path = media_dir / "video.mp4"
    media_path.write_bytes(b"test")

    facade.current_album = DummyAlbum(album_root)
    model._album_root = album_root
    model._active_filter = "videos"

    rows = [{"rel": "sub/video.mp4", "is_video": True, "is_live": False, "id": "1"}]

    entries = model._build_entries_from_scan_rows(album_root, rows, set())
    assert len(entries) == 1
    assert entries[0]["rel"] == "sub/video.mp4"

    # Existing entry should be deduped
    model._state_manager.set_rows([{"rel": "sub/video.mp4"}])
    entries = model._build_entries_from_scan_rows(album_root, rows, set())
    assert entries == []
