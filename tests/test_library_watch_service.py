from __future__ import annotations

from pathlib import Path

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
