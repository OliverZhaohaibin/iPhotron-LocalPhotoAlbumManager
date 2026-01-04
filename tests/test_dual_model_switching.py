import json
import os
from pathlib import Path
from types import SimpleNamespace

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
    assert facade.asset_list_model == facade._album_list_model


def test_open_library_root_reuses_cached_model(monkeypatch, tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()

    facade = AppFacade()
    facade._library_manager = SimpleNamespace(
        root=lambda: root,
        is_scanning_path=lambda _path: False,
        start_scanning=lambda *_args, **_kwargs: None,
        stop_scanning=lambda: None,
        pause_watcher=lambda: None,
        resume_watcher=lambda: None,
    )

    facade._library_update_service.consume_forced_reload = lambda _path: False
    facade._library_update_service.reset_cache = lambda: None
    facade._library_update_service.cancel_active_scan = lambda: None

    def _fake_open_album(path: Path, autoscan: bool = False, library_root: Path | None = None):
        return SimpleNamespace(root=path, manifest={"title": path.name})

    class _StubIndexStore:
        def __init__(self, *_args, **_kwargs) -> None:
            return

        def read_all(self):
            return iter([{"id": 1}])

    restart_calls: list[Path] = []

    def _record_restart(album_root, announce_index=True, force_reload=False):
        restart_calls.append(album_root)

    facade._restart_asset_load = _record_restart
    monkeypatch.setattr("src.iPhoto.gui.facade.backend.open_album", _fake_open_album)
    monkeypatch.setattr("src.iPhoto.gui.facade.backend.IndexStore", _StubIndexStore)

    facade.open_album(root)

    library_model = facade._library_list_model
    library_model._album_root = root
    library_model._state_manager.set_rows(
        [{"rel": "a.jpg", "abs": str(root / "a.jpg"), "id": 1}]
    )
    library_model._is_valid = True

    restart_calls.clear()
    facade.open_album(root)

    assert restart_calls == []
