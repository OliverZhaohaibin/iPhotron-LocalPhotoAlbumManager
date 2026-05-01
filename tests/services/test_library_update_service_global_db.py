from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import iPhoto.gui.services.library_update_service as lus
from iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE


class DummyAlbum:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.manifest = {}


class DummyLibrary:
    def __init__(
        self,
        root: Path | None,
        scan_service=None,
        lifecycle_service=None,
        *,
        is_scanning: bool = False,
    ) -> None:
        self._root = Path(root) if root is not None else None
        self.scan_service = scan_service
        self.asset_lifecycle_service = lifecycle_service
        self.started: list[tuple[Path, list[str], list[str]]] = []
        self._is_scanning = is_scanning

    def root(self) -> Path | None:
        return self._root

    def is_scanning_path(self, _path: Path) -> bool:
        return self._is_scanning

    def start_scanning(self, root: Path, include, exclude) -> None:
        self.started.append((Path(root), list(include), list(exclude)))


class FakeScanService:
    def __init__(self) -> None:
        self.prepared: list[dict] = []
        self.scanned: list[tuple[Path, bool]] = []
        self.finalized: list[tuple[Path, list[dict]]] = []

    def prepare_album_open(self, root: Path, **kwargs):
        self.prepared.append({"root": Path(root), **kwargs})
        return SimpleNamespace(asset_count=1, scanned=False)

    def scan_album(self, root: Path, *, persist_chunks: bool):
        self.scanned.append((root, persist_chunks))
        return SimpleNamespace(rows=[{"rel": "a.jpg"}])

    def finalize_scan(self, root: Path, rows: list[dict]) -> None:
        self.finalized.append((root, rows))


class FakeLifecycleService:
    def __init__(self) -> None:
        self.reconciled: list[tuple[Path, list[dict]]] = []
        self.media_failures: list[Path] = []

    def reconcile_missing_scan_rows(self, root: Path, rows: list[dict]) -> int:
        self.reconciled.append((root, rows))
        return 0

    def repair_missing_asset(self, path: Path) -> Path | None:
        self.media_failures.append(Path(path))
        return Path(path).parent


class FakeFallbackScanService(FakeScanService):
    instances: ClassVar[list["FakeFallbackScanService"]] = []

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = Path(root)
        self.synced: list[Path] = []
        self.instances.append(self)

    def sync_manifest_favorites(self, root: Path) -> None:
        self.synced.append(Path(root))


class FakeOpenScanService(FakeScanService):
    instances: ClassVar[list["FakeOpenScanService"]] = []

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = Path(root)
        self.instances.append(self)

    def prepare_album_open(self, root: Path, **kwargs):
        self.prepared.append({"root": Path(root), **kwargs})
        return SimpleNamespace(asset_count=0, scanned=False)


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
    lifecycle_service = FakeLifecycleService()

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(
            lib_root,
            scan_service=scan_service,
            lifecycle_service=lifecycle_service,
        ),
    )

    rows = service.rescan_album(DummyAlbum(album_root))

    assert rows == [{"rel": "a.jpg"}]
    assert scan_service.scanned == [(album_root, False)]
    assert scan_service.finalized == [(album_root, [{"rel": "a.jpg"}])]
    assert lifecycle_service.reconciled == [(album_root, [{"rel": "a.jpg"}])]


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


def test_prepare_album_open_uses_session_scan_service(tmp_path: Path) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    lib_root.mkdir()
    album_root.mkdir()
    scan_service = FakeScanService()
    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(
            lib_root,
            scan_service=scan_service,
        ),
    )

    routing = service.prepare_album_open(
        album_root,
        autoscan=False,
        hydrate_index=False,
        sync_manifest_favorites=False,
    )

    assert routing.asset_count == 1
    assert routing.should_rescan_async is False
    assert scan_service.prepared == [
        {
            "root": album_root,
            "autoscan": False,
            "hydrate_index": False,
            "sync_manifest_favorites": False,
        }
    ]


def test_prepare_album_open_requests_async_rescan_when_scope_is_empty(
    monkeypatch,
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "Album"
    album_root.mkdir()
    FakeOpenScanService.instances.clear()
    monkeypatch.setattr(lus, "LibraryScanService", FakeOpenScanService)

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: None,
    )

    routing = service.prepare_album_open(
        album_root,
        autoscan=False,
        hydrate_index=False,
        sync_manifest_favorites=True,
    )

    assert routing.asset_count == 0
    assert routing.should_rescan_async is True
    assert len(FakeOpenScanService.instances) == 1
    assert FakeOpenScanService.instances[0].prepared == [
        {
            "root": album_root,
            "autoscan": False,
            "hydrate_index": False,
            "sync_manifest_favorites": True,
        }
    ]


def test_rescan_album_async_uses_library_manager_scan_entry(tmp_path: Path) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    lib_root.mkdir()
    album_root.mkdir()
    library = DummyLibrary(lib_root)
    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: library,
    )

    service.rescan_album_async(DummyAlbum(album_root))

    assert library.started == [
        (album_root, list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE))
    ]


def test_rescan_album_async_fallback_passes_library_root(monkeypatch, tmp_path: Path) -> None:
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
    service = lus.LibraryUpdateService(
        task_manager=task_manager,
        current_album_getter=lambda: None,
        library_manager_getter=lambda: None,
    )

    service.rescan_album_async(DummyAlbum(album_root))

    assert task_manager.submitted, "scan task should be submitted"
    worker = task_manager.submitted[0]["worker"]
    assert isinstance(worker, StubScannerWorker)
    assert worker.library_root is None
    assert worker.scan_service is None


def test_restore_rescan_worker_receives_session_scan_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    album_root.mkdir(parents=True)
    scan_service = FakeScanService()
    lifecycle_service = FakeLifecycleService()

    class StubRescanWorker:
        def __init__(
            self,
            root,
            signals,
            library_root=None,
            scan_service=None,
            asset_lifecycle_service=None,
        ) -> None:
            self.root = Path(root)
            self.library_root = library_root
            self.scan_service = scan_service
            self._asset_lifecycle_service = asset_lifecycle_service

    monkeypatch.setattr(lus, "RescanWorker", StubRescanWorker)

    task_manager = DummyTaskManager()
    service = lus.LibraryUpdateService(
        task_manager=task_manager,
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(
            lib_root,
            scan_service=scan_service,
            lifecycle_service=lifecycle_service,
        ),
    )

    service._refresh_restored_album(album_root, lib_root)

    assert task_manager.submitted
    worker = task_manager.submitted[0]["worker"]
    assert isinstance(worker, StubRescanWorker)
    assert worker.library_root == lib_root
    assert worker.scan_service is scan_service
    assert worker._asset_lifecycle_service is lifecycle_service


def test_handle_media_load_failure_uses_lifecycle_service(tmp_path: Path) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    asset_path = album_root / "missing.mov"
    album_root.mkdir(parents=True)
    lifecycle_service = FakeLifecycleService()
    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: DummyLibrary(
            lib_root,
            lifecycle_service=lifecycle_service,
        ),
    )

    refreshed = service.handle_media_load_failure(asset_path)

    assert refreshed == album_root
    assert lifecycle_service.media_failures == [asset_path]


def test_handle_media_load_failure_uses_current_album_root_when_library_root_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "album"
    nested_root = album_root / "nested"
    asset_path = nested_root / "missing.mov"
    nested_root.mkdir(parents=True)

    class ExistingLifecycleService:
        library_root = None

        def repair_missing_asset(self, _path: Path) -> Path | None:
            raise AssertionError(
                "standalone repair should use an album-root-scoped lifecycle service"
            )

    class ReplacementLifecycleService:
        instances: ClassVar[list["ReplacementLifecycleService"]] = []

        def __init__(self, root: Path, *, scan_service=None) -> None:
            self.library_root = Path(root)
            self.scan_service = scan_service
            self.media_failures: list[Path] = []
            self.__class__.instances.append(self)

        def repair_missing_asset(self, path: Path) -> Path | None:
            self.media_failures.append(Path(path))
            return Path(path).parent

    ReplacementLifecycleService.instances.clear()
    monkeypatch.setattr(lus, "LibraryAssetLifecycleService", ReplacementLifecycleService)

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: DummyAlbum(album_root),
        library_manager_getter=lambda: DummyLibrary(
            None,
            lifecycle_service=ExistingLifecycleService(),
        ),
    )

    refreshed = service.handle_media_load_failure(asset_path)

    assert refreshed == album_root
    assert len(ReplacementLifecycleService.instances) == 1
    replacement = ReplacementLifecycleService.instances[0]
    assert replacement.library_root == album_root
    assert replacement.media_failures == [asset_path]
