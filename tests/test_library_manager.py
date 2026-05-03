from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for library tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtTest", reason="Qt test helpers not available", exc_type=ImportError)

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from iPhoto.bootstrap.library_session import LibrarySession
from iPhoto.errors import AlbumDepthError, AlbumOperationError, LibraryUnavailableError
from iPhoto.library.manager import LibraryManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _write_manifest(path: Path, title: str) -> None:
    payload = {
        "schema": "iPhoto/album@1",
        "title": title,
        "filters": {},
    }
    manifest = path / ".iphoto.album.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")


def test_bind_and_scan_tree(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    manager = LibraryManager()
    spy = QSignalSpy(manager.treeUpdated)
    with pytest.raises(LibraryUnavailableError):
        manager.bind_path(root)
    album = root / "Trip"
    child = album / "Day1"
    child.mkdir(parents=True)
    _write_manifest(album, "Summer Trip")
    manager.bind_path(root)
    qapp.processEvents()
    assert spy.count() >= 1
    albums = manager.list_albums()
    assert len(albums) == 1
    assert albums[0].title == "Summer Trip"
    children = manager.list_children(albums[0])
    assert len(children) == 1
    assert children[0].level == 2
    assert children[0].title == "Day1"


def test_bind_path_relays_people_snapshot_events(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)

    snapshot_spy = QSignalSpy(manager.peopleSnapshotCommitted)
    index_spy = QSignalSpy(manager.peopleIndexUpdated)
    coordinator = manager._people_index_coordinator
    assert coordinator is not None

    event = object()
    coordinator.snapshotCommitted.emit(event)
    qapp.processEvents()

    assert snapshot_spy.count() == 1
    assert snapshot_spy.at(0)[0] is event
    assert index_spy.count() == 1


def test_bind_path_rebinds_people_snapshot_events_for_prebound_session(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    session = LibrarySession(root)
    manager.bind_library_session(session)

    manager.bind_path(root)
    qapp.processEvents()

    snapshot_spy = QSignalSpy(manager.peopleSnapshotCommitted)
    index_spy = QSignalSpy(manager.peopleIndexUpdated)
    coordinator = manager._people_index_coordinator
    assert coordinator is not None

    event = object()
    coordinator.snapshotCommitted.emit(event)
    qapp.processEvents()

    assert snapshot_spy.count() == 1
    assert snapshot_spy.at(0)[0] is event
    assert index_spy.count() == 1


def test_bind_path_auto_binds_headless_library_session(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()

    manager.bind_path(root)
    qapp.processEvents()

    assert manager.library_session is not None
    assert manager.library_session.library_root == root
    assert manager.scan_service is not None
    assert manager.asset_query_service is not None
    assert manager.asset_lifecycle_service is not None
    assert manager.location_service is not None


def test_create_and_rename_album(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)
    created = manager.create_album("Paris")
    assert created.level == 1
    assert (created.path / ".iphoto.album.json").exists()
    sub = manager.create_subalbum(created, "Day0")
    assert sub.level == 2
    with pytest.raises(AlbumDepthError):
        manager.create_subalbum(sub, "TooDeep")
    rename_spy = QSignalSpy(manager.albumRenamed)
    old_sub_path = sub.path
    with patch.object(manager, "stop_scanning", wraps=manager.stop_scanning) as stop_scanning:
        manager.rename_album(sub, "Arrival")
    qapp.processEvents()
    stop_scanning.assert_called_once_with()
    assert rename_spy.count() == 1
    assert rename_spy.at(0) == [old_sub_path, created.path / "Arrival"]
    refreshed_parent = next(
        node for node in manager.list_albums() if node.path == created.path
    )
    refreshed_children = manager.list_children(refreshed_parent)
    assert any(child.title == "Arrival" for child in refreshed_children)
    manifest_path = next(
        child.path / ".iphoto.album.json"
        for child in refreshed_children
        if child.title == "Arrival"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["title"] == "Arrival"


@pytest.mark.parametrize("reserved_name", [".iPhoto", ".iphoto", ".IPHOTO", ".Trash", "exported"])
def test_reserved_album_names_are_rejected_for_create_and_rename(
    tmp_path: Path, qapp: QApplication, reserved_name: str
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)

    created = manager.create_album("Trips")
    child = manager.create_subalbum(created, "Day1")

    with pytest.raises(AlbumOperationError, match="reserved for internal use"):
        manager.create_album(reserved_name)
    with pytest.raises(AlbumOperationError, match="reserved for internal use"):
        manager.create_subalbum(created, reserved_name)
    with pytest.raises(AlbumOperationError, match="reserved for internal use"):
        manager.rename_album(created, reserved_name)
    with pytest.raises(AlbumOperationError, match="reserved for internal use"):
        manager.rename_album(child, reserved_name)

    qapp.processEvents()

    albums = manager.list_albums()
    assert any(node.path == created.path and node.title == "Trips" for node in albums)
    refreshed_parent = next(node for node in albums if node.path == created.path)
    refreshed_children = manager.list_children(refreshed_parent)
    assert any(kid.path == child.path and kid.title == "Day1" for kid in refreshed_children)


@pytest.mark.parametrize("internal_name", [".iPhoto", ".iphoto", ".IPHOTO"])
def test_work_dir_case_variants_are_hidden_from_album_tree(
    tmp_path: Path, qapp: QApplication, internal_name: str
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    visible = root / "Trips"
    visible.mkdir()
    _write_manifest(visible, "Trips")
    internal = root / internal_name
    internal.mkdir()
    _write_manifest(internal, f"Internal {internal_name}")

    manager = LibraryManager()
    manager.bind_path(root)
    qapp.processEvents()

    albums = manager.list_albums()
    assert [node.title for node in albums] == ["Trips"]


def test_ensure_manifest_generates_defaults(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    album_dir = root / "NoManifest"
    album_dir.mkdir(parents=True)
    manager = LibraryManager()
    manager.bind_path(root)
    node = next(node for node in manager.list_albums() if node.path == album_dir)
    manifest_path = manager.ensure_manifest(node)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["title"] == "NoManifest"
    assert data["schema"] == "iPhoto/album@1"


def test_scan_finished_skips_prune_when_worker_failed(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)

    class _Worker:
        cancelled = False
        failed = True
        scan_service = Mock()

    spy = QSignalSpy(manager.scanFinished)
    manager._current_scanner_worker = _Worker()

    with patch.object(manager._scan_thread_pool, "start") as start_mock:
        manager._on_scan_finished(root, [])
        qapp.processEvents()

    _Worker.scan_service.finalize_scan_result.assert_not_called()
    start_mock.assert_not_called()
    assert spy.count() == 1
    assert spy.at(0)[1] is False
