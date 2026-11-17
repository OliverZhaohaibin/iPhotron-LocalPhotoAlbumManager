from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.library.watch_service import LibraryWatchRequest, _LibraryWatchWorker


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
    queue_scans.assert_called_once_with([tmp_path], requeue_active=True)

    queue_scans.reset_mock()
    (tmp_path / "photo.ipo").touch()
    notify_change()
    queue_scans.assert_not_called()

    photo.unlink()
    notify_change()
    queue_scans.assert_called_once_with([tmp_path], requeue_active=True)
