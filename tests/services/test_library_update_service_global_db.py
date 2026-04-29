from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import iPhoto.gui.services.library_update_service as lus


class DummyAlbum:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.manifest = {}


class DummyLibrary:
    def __init__(self, root: Path, scan_service=None) -> None:
        self._root = Path(root)
        self.scan_service = scan_service

    def root(self) -> Path:
        return self._root


class FakeScanService:
    def __init__(self) -> None:
        self.scanned: list[tuple[Path, bool]] = []
        self.finalized: list[tuple[Path, list[dict]]] = []

    def scan_album(self, root: Path, *, persist_chunks: bool):
        self.scanned.append((root, persist_chunks))
        return SimpleNamespace(rows=[{"rel": "a.jpg"}])

    def finalize_scan(self, root: Path, rows: list[dict]) -> None:
        self.finalized.append((root, rows))


class FakeFallbackScanService(FakeScanService):
    instances: ClassVar[list["FakeFallbackScanService"]] = []

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = Path(root)
        self.synced: list[Path] = []
        self.instances.append(self)

    def sync_manifest_favorites(self, root: Path) -> None:
        self.synced.append(Path(root))


class DummyTaskManager:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def submit_task(self, **kwargs) -> None:
        self.submitted.append(kwargs)


def test_rescan_album_uses_session_scan_service(tmp_path: Path) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    lib_root.mkdir()
    album_root.mkdir()
    scan_service = FakeScanService()

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(lib_root, scan_service=scan_service),
    )

    rows = service.rescan_album(DummyAlbum(album_root))

    assert rows == [{"rel": "a.jpg"}]
    assert scan_service.scanned == [(album_root, False)]
    assert scan_service.finalized == [(album_root, [{"rel": "a.jpg"}])]


def test_rescan_album_fallback_syncs_manifest_favorites(
    monkeypatch,
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "Album"
    album_root.mkdir()
    FakeFallbackScanService.instances.clear()
    monkeypatch.setattr(lus, "LibraryScanService", FakeFallbackScanService)

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: None,
    )

    rows = service.rescan_album(DummyAlbum(album_root))

    assert rows == [{"rel": "a.jpg"}]
    assert len(FakeFallbackScanService.instances) == 1
    fallback = FakeFallbackScanService.instances[0]
    assert fallback.root == album_root
    assert fallback.scanned == [(album_root, False)]
    assert fallback.finalized == [(album_root, [{"rel": "a.jpg"}])]
    assert fallback.synced == [album_root]


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
        def __init__(
            self,
            root,
            include,
            exclude,
            signals,
            library_root=None,
            scan_service=None,
        ) -> None:
            self.root = Path(root)
            self.library_root = library_root
            self.scan_service = scan_service
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
    scan_service = FakeScanService()
    service = lus.LibraryUpdateService(
        task_manager=task_manager,
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(lib_root, scan_service=scan_service),
    )

    service.rescan_album_async(DummyAlbum(album_root))

    assert task_manager.submitted, "scan task should be submitted"
    worker = task_manager.submitted[0]["worker"]
    assert isinstance(worker, StubScannerWorker)
    assert worker.library_root == lib_root
    assert worker.scan_service is scan_service


def test_restore_rescan_worker_receives_session_scan_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    album_root.mkdir(parents=True)
    scan_service = FakeScanService()

    class StubRescanWorker:
        def __init__(self, root, signals, library_root=None, scan_service=None) -> None:
            self.root = Path(root)
            self.library_root = library_root
            self.scan_service = scan_service

    monkeypatch.setattr(lus, "RescanWorker", StubRescanWorker)

    task_manager = DummyTaskManager()
    service = lus.LibraryUpdateService(
        task_manager=task_manager,
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(lib_root, scan_service=scan_service),
    )

    service._refresh_restored_album(album_root, lib_root)

    assert task_manager.submitted
    worker = task_manager.submitted[0]["worker"]
    assert isinstance(worker, StubRescanWorker)
    assert worker.library_root == lib_root
    assert worker.scan_service is scan_service
