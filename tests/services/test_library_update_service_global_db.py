from pathlib import Path
from unittest.mock import MagicMock, patch

import iPhoto.gui.services.library_update_service as lus


class DummyAlbum:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.manifest = {}


class DummyLibrary:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def root(self) -> Path:
        return self._root


class DummyTaskManager:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def submit_task(self, **kwargs) -> None:
        self.submitted.append(kwargs)


def test_rescan_album_uses_library_root(monkeypatch, tmp_path: Path) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    lib_root.mkdir()
    album_root.mkdir()

    calls: list[tuple[Path, Path | None]] = []

    def fake_execute(self, root: Path) -> list:
        calls.append((Path(root), self._library_root_getter()))
        return []

    monkeypatch.setattr(lus.RescanAlbumUseCase, "execute", fake_execute)

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(lib_root),
    )

    service.rescan_album(DummyAlbum(album_root))

    assert calls == [(album_root, lib_root)]


def test_rescan_album_async_passes_library_root(monkeypatch, tmp_path: Path) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    lib_root.mkdir()
    album_root.mkdir()

    class StubSignal:
        def connect(self, *_args, **_kwargs):
            return None

    class StubScannerSignals:
        def __init__(self) -> None:
            self.progressUpdated = StubSignal()
            self.chunkReady = StubSignal()
            self.finished = StubSignal()
            self.error = StubSignal()
            self.batchFailed = StubSignal()

    class StubScannerWorker:
        def __init__(self, root, include, exclude, signals, library_root=None) -> None:
            self.root = Path(root)
            self.library_root = library_root
            self._is_cancelled = False
            self._had_error = False

        @property
        def cancelled(self) -> bool:
            return self._is_cancelled

        @property
        def failed(self) -> bool:
            return self._had_error

        def cancel(self) -> None:
            self._is_cancelled = True

    monkeypatch.setattr(lus, "ScannerSignals", StubScannerSignals)
    monkeypatch.setattr(lus, "ScannerWorker", StubScannerWorker)

    task_manager = DummyTaskManager()
    service = lus.LibraryUpdateService(
        task_manager=task_manager,
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(lib_root),
    )

    service.rescan_album_async(DummyAlbum(album_root))

    assert task_manager.submitted, "scan task should be submitted"
    worker = task_manager.submitted[0]["worker"]
    assert isinstance(worker, StubScannerWorker)
    assert worker.library_root == lib_root
