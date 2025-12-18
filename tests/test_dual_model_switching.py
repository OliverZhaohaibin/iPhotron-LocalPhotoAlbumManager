import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.facade import AppFacade
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


def test_dual_model_switching(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    album_dir = root / "Trip"
    album_dir.mkdir(parents=True)
    _write_manifest(root, "Library")
    _write_manifest(album_dir, "Trip")

    manager = LibraryManager()
    manager.bind_path(root)

    facade = AppFacade()
    facade.bind_library(manager)

    # Check initial state

    # 1. Open Library Root (All Photos)
    facade.open_album(root)
    qapp.processEvents()
    library_model = facade.asset_list_model
    assert library_model is not None
    assert facade._active_model == facade._library_list_model

    # 2. Open Sub-Album
    facade.open_album(album_dir)
    qapp.processEvents()
    album_model = facade.asset_list_model
    assert album_model is not None
    assert album_model != library_model
    assert facade._active_model == facade._album_list_model

    # 3. Switch back to Library Root
    facade.open_album(root)
    qapp.processEvents()
    current_model = facade.asset_list_model
    assert current_model == library_model
    assert facade._active_model == facade._library_list_model

    # Verify activeModelChanged signal logic
    signaled_models = []
    facade.activeModelChanged.connect(lambda m: signaled_models.append(m))

    # Switch to Album
    facade.open_album(album_dir)
    qapp.processEvents()
    assert len(signaled_models) == 1
    assert signaled_models[0] == album_model

    # Re-open same album (should NOT emit signal)
    facade.open_album(album_dir)
    qapp.processEvents()
    assert len(signaled_models) == 1

    # Switch to Library
    facade.open_album(root)
    qapp.processEvents()
    assert len(signaled_models) == 2
    assert signaled_models[1] == library_model

    # Re-open Library (should NOT emit signal)
    facade.open_album(root)
    qapp.processEvents()
    assert len(signaled_models) == 2

    # Rapid switching simulation
    facade.open_album(album_dir)
    qapp.processEvents()
    facade.open_album(root)
    qapp.processEvents()
    facade.open_album(album_dir)
    qapp.processEvents()


    facade.open_album(root)
    assert facade.asset_list_model == facade._library_list_model
