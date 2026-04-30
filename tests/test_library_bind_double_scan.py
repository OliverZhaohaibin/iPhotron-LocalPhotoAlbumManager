
import os
import json
from pathlib import Path
from unittest.mock import call, patch

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from iPhoto.library.manager import LibraryManager

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_watcher_active_during_init_deleted_dir(tmp_path, qapp):
    """
    Verify that the file system watcher is active during _initialize_deleted_dir
    when re-binding a library, which causes the double-scan issue.
    """
    root = tmp_path / "Library"
    root.mkdir()

    manager = LibraryManager()
    manager.bind_path(root)

    # After first bind, the root should be watched
    assert str(root) in manager._watcher.directories()

    # We want to verify that when we call bind_path AGAIN,
    # the watcher is still active when _initialize_deleted_dir is called.

    original_init = manager._initialize_deleted_dir

    watcher_was_active = False

    def side_effect():
        nonlocal watcher_was_active
        # Check if we are watching anything
        if manager._watcher.directories():
            watcher_was_active = True
        original_init()

    with patch.object(manager, '_initialize_deleted_dir', side_effect=side_effect):
        manager.bind_path(root)

    # WITHOUT THE FIX: assertion should be True (watcher was active)
    # WITH THE FIX: assertion should be False (watcher was cleared)
    assert watcher_was_active is False, "Watcher should NOT be active during init (bug fixed)"


def test_bind_path_cancels_scans_on_rebind(tmp_path, qapp):
    root = tmp_path / "Library"
    root.mkdir()

    manager = LibraryManager()
    manager.bind_path(root)

    with patch.object(manager, "stop_scanning") as stop_scanning:
        manager.bind_path(root)

    assert stop_scanning.called


def test_bind_path_emits_tree_updated_for_empty_library(tmp_path, qapp):
    """bind_path must emit treeUpdated even when the library has no album
    subdirectories.  Without this the AlbumTreeModel never transitions from
    the 'Bind Basic Library…' placeholder to the full sidebar tree."""

    root = tmp_path / "EmptyLibrary"
    root.mkdir()
    # Create only a regular file – no album subdirectories.
    (root / "photo.jpg").write_bytes(b"fake")

    manager = LibraryManager()
    spy = QSignalSpy(manager.treeUpdated)
    manager.bind_path(root)
    assert spy.count() >= 1, "treeUpdated must be emitted when binding an empty library"


def test_watcher_debounce_scans_changed_scope_through_session_service(
    tmp_path,
    qapp,
):
    root = tmp_path / "Library"
    album = root / "Album"
    album.mkdir(parents=True)
    (album / ".iphoto.album.json").write_text(
        json.dumps({"schema": "iPhoto/album@1", "title": "Album", "filters": {}}),
        encoding="utf-8",
    )

    class FakeScanService:
        def __init__(self) -> None:
            self.filter_roots = []

        def scan_filters(self, path):
            self.filter_roots.append(Path(path))
            return ["*.jpg"], []

    manager = LibraryManager()
    manager.bind_path(root)
    scan_service = FakeScanService()
    manager.bind_scan_service(scan_service)

    with patch.object(manager, "start_scanning") as start_scanning:
        manager._on_directory_changed(str(album))
        manager._on_watcher_debounce_timeout()

    assert scan_service.filter_roots == [album]
    start_scanning.assert_called_once_with(album, ["*.jpg"], [])


def test_root_watcher_event_for_new_album_uses_new_album_filters(
    tmp_path,
    qapp,
):
    root = tmp_path / "Library"
    root.mkdir()

    manager = LibraryManager()
    manager.bind_path(root)

    album = root / "NewAlbum"
    album.mkdir()
    (album / ".iphoto.album.json").write_text(
        json.dumps(
            {
                "schema": "iPhoto/album@1",
                "title": "NewAlbum",
                "filters": {"include": ["*.heic"], "exclude": ["*.mov"]},
            }
        ),
        encoding="utf-8",
    )

    class FakeScanService:
        def __init__(self) -> None:
            self.filter_roots = []

        def scan_filters(self, path):
            self.filter_roots.append(Path(path))
            return ["*.heic"], ["*.mov"]

    scan_service = FakeScanService()
    manager.bind_scan_service(scan_service)

    with patch.object(manager, "start_scanning") as start_scanning:
        manager._on_directory_changed(str(root))
        manager._on_watcher_debounce_timeout()

    assert scan_service.filter_roots == [album]
    start_scanning.assert_called_once_with(album, ["*.heic"], ["*.mov"])


def test_watcher_scans_multiple_changed_scopes_with_each_album_filter(
    tmp_path,
    qapp,
):
    root = tmp_path / "Library"
    album_a = root / "AlbumA"
    album_b = root / "AlbumB"
    album_a.mkdir(parents=True)
    album_b.mkdir(parents=True)
    for album in (album_a, album_b):
        (album / ".iphoto.album.json").write_text(
            json.dumps({"schema": "iPhoto/album@1", "title": album.name, "filters": {}}),
            encoding="utf-8",
        )

    class FakeScanService:
        def __init__(self) -> None:
            self.filter_roots = []

        def scan_filters(self, path):
            path = Path(path)
            self.filter_roots.append(path)
            return [f"*.{path.name.lower()}"], [f"skip-{path.name.lower()}"]

    manager = LibraryManager()
    manager.bind_path(root)
    scan_service = FakeScanService()
    manager.bind_scan_service(scan_service)

    with patch.object(manager, "start_scanning") as start_scanning:
        manager._on_directory_changed(str(root))
        manager._on_directory_changed(str(album_b))
        manager._on_directory_changed(str(album_a))
        manager._on_watcher_debounce_timeout()

        assert scan_service.filter_roots == [album_a]
        start_scanning.assert_called_once_with(
            album_a,
            ["*.albuma"],
            ["skip-albuma"],
        )

        manager._on_watcher_scan_finished(album_a, True)

    assert scan_service.filter_roots == [album_a, album_b]
    assert start_scanning.call_args_list == [
        call(album_a, ["*.albuma"], ["skip-albuma"]),
        call(album_b, ["*.albumb"], ["skip-albumb"]),
    ]


def test_watcher_pause_drops_previously_queued_paths(tmp_path, qapp):
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)

    with patch.object(manager, "start_scanning") as start_scanning:
        manager._on_directory_changed(str(root))
        assert manager._pending_watch_paths == {root}

        manager.pause_watcher()
        manager.resume_watcher()
        manager._on_watcher_debounce_timeout()

    assert manager._pending_watch_paths == set()
    start_scanning.assert_not_called()


def test_watcher_pause_suppresses_session_scan(tmp_path, qapp):
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryManager()
    manager.bind_path(root)

    with patch.object(manager, "start_scanning") as start_scanning:
        manager.pause_watcher()
        manager._on_directory_changed(str(root))
        manager.resume_watcher()
        manager._on_watcher_debounce_timeout()

    start_scanning.assert_not_called()
