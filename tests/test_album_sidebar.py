import base64
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for sidebar tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar
from src.iPhoto.library.manager import LibraryManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _write_manifest(path: Path, title: str) -> None:
    payload = {"schema": "iPhoto/album@1", "title": title, "filters": {}}
    (path / ".iphoto.album.json").write_text(json.dumps(payload), encoding="utf-8")


def test_programmatic_selection_suppresses_signals(tmp_path: Path, qapp: QApplication) -> None:
    """Verify that programmatic selection calls do not emit navigation signals."""
    root = tmp_path / "Library"
    album_dir = root / "Trip"
    album_dir.mkdir(parents=True)
    _write_manifest(album_dir, "Trip")
    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    sidebar = AlbumSidebar(manager)
    # Force the sidebar to process pending events (e.g. tree population)
    qapp.processEvents()

    triggered_all: list[bool] = []
    triggered_static: list[str] = []
    triggered_album: list[Path] = []

    sidebar.allPhotosSelected.connect(lambda: triggered_all.append(True))
    sidebar.staticNodeSelected.connect(lambda title: triggered_static.append(title))
    sidebar.albumSelected.connect(lambda path: triggered_album.append(path))

    # Test: Selecting "All Photos" programmatically
    sidebar.select_all_photos()
    qapp.processEvents()
    assert not triggered_all, "Programmatic All Photos selection must suppress signal"

    # Test: Selecting static node "Videos" programmatically
    sidebar.select_static_node("Videos")
    qapp.processEvents()
    assert not triggered_static, "Programmatic static node selection must suppress signal"

    # Test: Selecting album path programmatically
    sidebar.select_path(album_dir)
    qapp.processEvents()
    assert not triggered_album, "Programmatic album selection must suppress signal"
