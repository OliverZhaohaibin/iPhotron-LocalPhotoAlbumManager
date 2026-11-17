from __future__ import annotations

from pathlib import Path
from unicodedata import normalize
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store import get_global_repository
from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.library.watch_service import (
    LibraryWatchRequest,
    LibraryWatchResult,
    _LibraryWatchWorker,
)


class _FakeWatcher:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def directories(self) -> list[str]:
        return list(self.paths)

    def removePaths(self, paths: list[str]) -> None:  # noqa: N802
        removed = set(paths)
        self.paths = [path for path in self.paths if path not in removed]

    def addPaths(self, paths: list[str]) -> None:  # noqa: N802
        self.paths.extend(paths)


class _FakeTimer:
    def __init__(self) -> None:
        self.started = False

    def start(self, *_args) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


def test_configure_completes_a_truncated_prepared_tree(qapp, tmp_path: Path) -> None:
    root = tmp_path / "library"
    first = root / "A"
    second = root / "B"
    first.mkdir(parents=True)
    second.mkdir()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._batch_timer = _FakeTimer()  # type: ignore[assignment]
    worker._poll_timer = _FakeTimer()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)

    worker.configure(LibraryWatchRequest(1, root, (root, first), False))

    assert len(results) == 1
    assert {node.path for node in results[0].albums} == {first, second}
    assert set(worker._request.paths) == {root, first, second}


def test_poll_reports_the_existing_album_whose_signature_changed(qapp) -> None:
    root = Path("/library")
    album = root / "Album"
    worker = _LibraryWatchWorker()
    worker._request = LibraryWatchRequest(4, root, (root, album), True)
    worker._poll_signature = (
        (str(root), 1, 0),
        (str(album), 1, 0),
    )
    worker._signature = lambda _paths: (  # type: ignore[method-assign]
        (str(root), 1, 0),
        (str(album), 2, 0),
    )
    changed: list[tuple[Path, ...]] = []
    worker._emit_snapshot = changed.append  # type: ignore[method-assign]

    worker._poll()

    assert changed == [(album,)]


@pytest.mark.parametrize("polling", [False, True])
def test_root_media_changes_queue_scans_but_internal_writes_do_not(
    qapp, tmp_path: Path, polling: bool
) -> None:
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._batch_timer = _FakeTimer()  # type: ignore[assignment]
    worker._poll_timer = _FakeTimer()  # type: ignore[assignment]
    controller = LibraryRuntimeController()
    controller._root = tmp_path
    controller._background_watch_generation = 1
    controller._queue_watcher_scan_roots = queue_scans = Mock()
    worker.resultReady.connect(controller._on_background_watch_result)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), polling))

    def notify_change() -> None:
        if polling:
            worker._poll()
        else:
            worker._on_directory_changed(str(tmp_path))

    (tmp_path / "links.json").touch()
    notify_change()
    queue_scans.assert_not_called()

    photo = tmp_path / "photo.jpg"
    photo.touch()
    notify_change()
    queue_scans.assert_called_once_with([tmp_path], root_only=True)

    queue_scans.reset_mock()
    (tmp_path / "photo.ipo").touch()
    notify_change()
    queue_scans.assert_not_called()

    photo.unlink()
    notify_change()
    queue_scans.assert_called_once_with([tmp_path], root_only=True)


@pytest.mark.parametrize("polling", [False, True])
def test_poll_detects_root_media_overwrite_without_a_directory_change(
    qapp, tmp_path: Path, polling: bool
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"before")
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    worker._poll_timer = timer = _FakeTimer()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), polling))
    assert timer.started
    worker._signature = Mock(return_value=worker._poll_signature)

    photo.write_bytes(b"after overwrite")
    worker._poll()

    assert len(results) == 2
    assert results[-1].root_media_changed
    worker._poll()
    assert len(results) == 2
    if not polling:
        worker._signature.assert_not_called()


def test_root_directory_notification_accepts_equivalent_path(qapp, tmp_path: Path) -> None:
    root = tmp_path / normalize("NFD", "Café")
    root.mkdir()
    alternate_path = normalize("NFC", str(root))
    try:
        if not Path(alternate_path).samefile(root):
            pytest.skip("filesystem distinguishes Unicode spellings")
    except OSError:
        pytest.skip("filesystem distinguishes Unicode spellings")
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, root, (root,), False))

    (root / "photo.jpg").touch()
    worker._on_directory_changed(alternate_path)

    assert results[-1].changed_paths == (root,)
    assert results[-1].root_media_changed


def test_root_watcher_uses_session_scan_filters(qapp, tmp_path: Path) -> None:
    (tmp_path / ".iphoto.album.json").write_text(
        '{"schema":"iPhoto/album@1","title":"Custom",'
        '"filters":{"include":["**/*.jpeg"],"exclude":["**/*.bak"]}}',
        encoding="utf-8",
    )
    service = LibraryScanService(tmp_path)
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), False, service.scan_filters))

    (tmp_path / "ignored.jpg").touch()
    worker._on_directory_changed(str(tmp_path))
    assert not results[-1].root_media_changed

    (tmp_path / "._slide.jpeg").touch()
    worker._on_directory_changed(str(tmp_path))
    assert results[-1].root_media_changed


def test_unreadable_root_media_preserves_signature_and_album_snapshot(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.touch()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path,), False))
    previous_media = worker._root_media

    original_stat = Path.stat

    def failing_stat(path: Path, *args, **kwargs):
        if path == photo:
            raise PermissionError("Temporarily unreadable")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    album = tmp_path / "Album"
    album.mkdir()
    worker._on_directory_changed(str(tmp_path))

    assert results[-1].warning is None
    assert [node.path for node in results[-1].albums] == [album]
    assert not results[-1].root_media_changed
    assert worker._root_media == previous_media


def test_unreadable_child_album_does_not_block_root_scan(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    controller = LibraryRuntimeController()
    controller._root = tmp_path
    controller._background_watch_generation = 1
    controller._queue_watcher_scan_roots = queue_scans = Mock()
    worker.resultReady.connect(controller._on_background_watch_result)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path, album), False))
    queue_scans.reset_mock()
    original_iterdir = Path.iterdir

    def failing_iterdir(path: Path):
        if path == album:
            raise PermissionError("Temporarily unreadable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", failing_iterdir)
    (tmp_path / "photo.jpg").touch()
    worker._on_directory_changed(str(tmp_path))
    worker._poll()

    queue_scans.assert_called_once_with([tmp_path], root_only=True)
    assert [node.path for node in controller.list_albums()] == [album]
    controller.shutdown()


@pytest.mark.parametrize("polling", [False, True])
def test_tree_refresh_retries_after_a_temporary_read_failure(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, polling: bool
) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    worker = _LibraryWatchWorker()
    worker._watcher = _FakeWatcher()  # type: ignore[assignment]
    results = []
    worker.resultReady.connect(results.append)
    worker.configure(LibraryWatchRequest(1, tmp_path, (tmp_path, album), polling))
    new_album = tmp_path / "New Album"
    new_album.mkdir()
    (album / "new.jpg").touch()
    (tmp_path / "root.jpg").touch()
    original_iterdir = Path.iterdir

    def failing_iterdir(path: Path):
        if path == album:
            raise PermissionError("Temporarily unreadable")
        return original_iterdir(path)

    with monkeypatch.context() as failing:
        failing.setattr(Path, "iterdir", failing_iterdir)
        if polling:
            worker._poll()
        else:
            worker._on_directory_changed(str(album))
            worker._on_directory_changed(str(tmp_path))
        assert results[-1].warning is not None
        assert results[-1].root_media_changed

    worker._poll()

    assert results[-1].warning is None
    assert {node.path for node in results[-1].albums} == {album, new_album}
    assert album in results[-1].changed_paths
    assert set(worker._request.paths) == {tmp_path, album, new_album}
    count = len(results)
    worker._poll()
    assert len(results) == count


def test_root_watcher_scan_preserves_child_album_assets(qapp, tmp_path: Path) -> None:
    root = tmp_path.resolve()
    album = root / "Custom"
    album.mkdir()
    (album / ".iphoto.album.json").write_text(
        '{"schema":"iPhoto/album@1","title":"Custom","filters":{"include":["**/*.tiff"]}}',
        encoding="utf-8",
    )
    source = Image.new("RGB", (16, 16), "red")
    source.save(album / "saved.tiff")
    source.save(album / "excluded.jpg")
    source.save(root / "root.jpg")
    controller = LibraryRuntimeController()
    try:
        controller.bind_path(root)
        controller._background_watch_generation = 1
        scan_service = controller.scan_service
        assert scan_service is not None
        scan_service.rescan_album(album, pair_live=False)
        repository = get_global_repository(root)
        repository.set_favorite_status("Custom/saved.tiff", True)
        snapshot = LibraryWatchResult(
            1, (root,), tuple(controller.list_albums()), root_media_changed=True
        )
        finished = []
        controller.scanFinished.connect(lambda path, ok: finished.append((path, ok)))

        with (
            patch.object(
                controller._scan_thread_pool, "start", side_effect=lambda task: task.run()
            ),
            patch.object(repository, "read_all", wraps=repository.read_all) as read_all,
            patch.object(repository, "apply_live_role_updates") as replace_all_roles,
        ):
            controller._on_background_watch_result(snapshot)
            assert repository.get_rows_by_rels(["root.jpg"])
            (root / "root.jpg").unlink()
            controller._on_background_watch_result(snapshot)

        assert finished == [(root, True), (root, True)]
        read_all.assert_not_called()
        replace_all_roles.assert_not_called()
        rows = repository.get_rows_by_rels(["root.jpg", "Custom/saved.tiff", "Custom/excluded.jpg"])
        assert set(rows) == {"Custom/saved.tiff"}
        assert rows["Custom/saved.tiff"]["is_favorite"]
        assert not scan_service.is_scan_scope_complete(root)
    finally:
        controller.shutdown()
