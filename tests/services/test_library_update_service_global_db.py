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

    def start_session_scan(self, root: Path, *, include, exclude) -> None:
        self.start_scanning(root, include, exclude)


class FakeScanService:
    def __init__(self, library_root: Path | None = None) -> None:
        self.library_root = Path(library_root) if library_root is not None else None
        self.prepared: list[dict] = []
        self.rescanned: list[dict] = []
        self.paired: list[Path] = []

    def prepare_album_open(self, root: Path, **kwargs):
        self.prepared.append({"root": Path(root), **kwargs})
        return SimpleNamespace(asset_count=1, scanned=False)

    def rescan_album(self, root: Path, **kwargs):
        self.rescanned.append({"root": Path(root), **kwargs})
        return [{"rel": "a.jpg"}]

    def pair_album(self, root: Path):
        self.paired.append(Path(root))
        return [SimpleNamespace(root=Path(root))]


class FakeLifecycleService:
    def __init__(self, library_root: Path | None = None) -> None:
        self.library_root = Path(library_root) if library_root is not None else None
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
        super().__init__(root)
        self.root = Path(root)
        self.synced: list[Path] = []
        self.instances.append(self)

    def rescan_album(self, root: Path, **kwargs):
        self.rescanned.append({"root": Path(root), **kwargs})
        if kwargs.get("sync_manifest_favorites"):
            self.synced.append(Path(root))
        return [{"rel": "a.jpg"}]


class FakeOpenScanService(FakeScanService):
    instances: ClassVar[list["FakeOpenScanService"]] = []

    def __init__(self, root: Path) -> None:
        super().__init__(root)
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
    scan_service = FakeScanService(lib_root)
    lifecycle_service = FakeLifecycleService(lib_root)

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
    assert scan_service.rescanned == [
        {
            "root": album_root,
            "sync_manifest_favorites": False,
            "pair_live": True,
        }
    ]
    assert lifecycle_service.reconciled == []


def test_rescan_album_fallback_syncs_manifest_favorites(
    monkeypatch,
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "Album"
    album_root.mkdir()
    FakeFallbackScanService.instances.clear()
    monkeypatch.setattr(
        lus,
        "create_standalone_scan_service",
        lambda root: FakeFallbackScanService(root),
    )

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
    assert fallback.rescanned == [
        {
            "root": album_root,
            "sync_manifest_favorites": True,
            "pair_live": True,
        }
    ]
    assert fallback.synced == [album_root]


def test_prepare_album_open_uses_session_scan_service(tmp_path: Path) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    lib_root.mkdir()
    album_root.mkdir()
    scan_service = FakeScanService(lib_root)
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
    tmp_path: Path,
) -> None:
    lib_root = tmp_path / "Library"
    album_root = lib_root / "Album"
    lib_root.mkdir()
    album_root.mkdir()
    scan_service = FakeOpenScanService(lib_root)

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
        sync_manifest_favorites=True,
    )

    assert routing.asset_count == 0
    assert routing.should_rescan_async is True
    assert scan_service.prepared == [
        {
            "root": album_root,
            "autoscan": False,
            "hydrate_index": False,
            "sync_manifest_favorites": True,
        }
    ]


def test_rescan_album_async_routes_bound_library_scans_via_library_manager(
    tmp_path: Path,
) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    lib_root.mkdir()
    album_root.mkdir()
    scan_service = FakeScanService(lib_root)
    library = DummyLibrary(
        lib_root,
        scan_service=scan_service,
    )

    class FakeTaskRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def is_scanning_path(self, _path: Path) -> bool:
            return False

        def start_scan(self, **kwargs) -> None:
            self.calls.append(kwargs)

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: library,
    )
    runner = FakeTaskRunner()
    service._task_runner = runner

    service.rescan_album_async(DummyAlbum(album_root))

    assert runner.calls == []
    assert library.started == [
        (album_root, list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE))
    ]


def test_rescan_album_async_fallback_uses_standalone_scan_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    scan_service = FakeFallbackScanService(tmp_path / "Album")

    class FakeTaskRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def is_scanning_path(self, _path: Path) -> bool:
            return False

        def start_scan(self, **kwargs) -> None:
            self.calls.append(kwargs)

    task_manager = DummyTaskManager()
    service = lus.LibraryUpdateService(
        task_manager=task_manager,
        current_album_getter=lambda: None,
        library_manager_getter=lambda: None,
    )
    monkeypatch.setattr(
        lus,
        "create_standalone_scan_service",
        lambda root: scan_service,
    )
    runner = FakeTaskRunner()
    service._task_runner = runner

    album_root = tmp_path / "Album"
    album_root.mkdir()
    service.rescan_album_async(DummyAlbum(album_root))

    assert runner.calls
    call = runner.calls[0]
    assert call["root"] == album_root
    assert list(call["include"]) == list(DEFAULT_INCLUDE)
    assert list(call["exclude"]) == list(DEFAULT_EXCLUDE)
    assert call["library_root"] is None
    assert call["scan_service"] is scan_service


def test_scan_root_async_fallback_relays_batch_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    scan_service = FakeFallbackScanService(tmp_path / "Album")

    class FakeTaskRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def is_scanning_path(self, _path: Path) -> bool:
            return False

        def start_scan(self, **kwargs) -> None:
            self.calls.append(kwargs)

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: None,
    )
    monkeypatch.setattr(
        lus,
        "create_standalone_scan_service",
        lambda root: scan_service,
    )
    runner = FakeTaskRunner()
    service._task_runner = runner

    album_root = tmp_path / "Album"
    album_root.mkdir()
    failures: list[tuple[Path, int]] = []
    service.scanBatchFailed.connect(lambda root, count: failures.append((root, count)))

    service.scan_root_async(
        album_root,
        include=DEFAULT_INCLUDE,
        exclude=DEFAULT_EXCLUDE,
    )

    assert runner.calls
    runner.calls[0]["on_batch_failed"](album_root, 2)
    assert failures == [(album_root, 2)]


def test_restore_rescan_worker_receives_session_scan_service(
    tmp_path: Path,
) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    album_root.mkdir(parents=True)
    scan_service = FakeScanService(lib_root)
    lifecycle_service = FakeLifecycleService(lib_root)

    class FakeTaskRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def start_restore_refresh(self, **kwargs) -> None:
            self.calls.append(kwargs)

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
    runner = FakeTaskRunner()
    service._task_runner = runner

    service._refresh_restored_album(album_root, lib_root)

    assert runner.calls
    call = runner.calls[0]
    assert call["root"] == album_root
    assert call["library_root"] == lib_root
    assert call["scan_service"] is scan_service


def test_handle_media_load_failure_uses_lifecycle_service(tmp_path: Path) -> None:
    lib_root = tmp_path / "library"
    album_root = lib_root / "Album"
    asset_path = album_root / "missing.mov"
    album_root.mkdir(parents=True)
    lifecycle_service = FakeLifecycleService(lib_root)
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


def test_handle_media_load_failure_falls_back_to_standalone_lifecycle_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "album"
    nested_root = album_root / "nested"
    asset_path = nested_root / "missing.mov"
    nested_root.mkdir(parents=True)
    fallback_lifecycle = FakeLifecycleService(album_root)
    created: list[tuple[Path, object | None]] = []

    def _create_lifecycle(root: Path, *, scan_service=None):
        created.append((Path(root), scan_service))
        return fallback_lifecycle

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: DummyAlbum(album_root),
        library_manager_getter=lambda: DummyLibrary(
            None,
        ),
    )
    monkeypatch.setattr(
        lus,
        "create_standalone_asset_lifecycle_service",
        _create_lifecycle,
    )
    errors: list[str] = []
    service.errorRaised.connect(errors.append)

    refreshed = service.handle_media_load_failure(asset_path)

    assert refreshed == album_root
    assert created == [(album_root, None)]
    assert fallback_lifecycle.media_failures == [asset_path]
    assert errors == []


def test_handle_media_load_failure_uses_standalone_for_album_outside_bound_library(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lib_root = tmp_path / "Library"
    album_root = tmp_path / "Standalone"
    nested_root = album_root / "nested"
    asset_path = nested_root / "missing.mov"
    lib_root.mkdir()
    nested_root.mkdir(parents=True)
    bound_lifecycle = FakeLifecycleService(lib_root)
    fallback_lifecycle = FakeLifecycleService(album_root)
    created: list[tuple[Path, object | None]] = []

    def _create_lifecycle(root: Path, *, scan_service=None):
        created.append((Path(root), scan_service))
        return fallback_lifecycle

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: DummyAlbum(album_root),
        library_manager_getter=lambda: DummyLibrary(
            lib_root,
            lifecycle_service=bound_lifecycle,
        ),
    )
    monkeypatch.setattr(
        lus,
        "create_standalone_asset_lifecycle_service",
        _create_lifecycle,
    )

    refreshed = service.handle_media_load_failure(asset_path)

    assert refreshed == album_root
    assert created == [(album_root, None)]
    assert fallback_lifecycle.media_failures == [asset_path]
    assert bound_lifecycle.media_failures == []


def test_scan_completion_uses_runtime_finalize_hook(tmp_path: Path) -> None:
    album_root = tmp_path / "Album"
    album_root.mkdir()

    class FinalizeScanService:
        def __init__(self) -> None:
            self.completed: list[tuple[Path, list[dict], bool]] = []

        def finalize_scan_result(self, root: Path, rows: list[dict], *, pair_live: bool):
            self.completed.append((Path(root), list(rows), pair_live))
            return list(rows)

    service = lus.LibraryUpdateService(
        task_manager=DummyTaskManager(),
        current_album_getter=lambda: None,
        library_manager_getter=lambda: None,
    )
    scan_service = FinalizeScanService()
    completion = lus.ScanTaskCompletion(
        root=album_root,
        rows=[{"rel": "a.jpg"}],
        scan_service=scan_service,
        library_root=None,
    )

    index_updates: list[Path] = []
    link_updates: list[Path] = []
    finished: list[tuple[Path, bool]] = []
    service.indexUpdated.connect(index_updates.append)
    service.linksUpdated.connect(link_updates.append)
    service.scanFinished.connect(lambda root, ok: finished.append((root, ok)))

    service._on_scan_completed(completion)

    assert scan_service.completed == [(album_root, [{"rel": "a.jpg"}], True)]
    assert index_updates == [album_root]
    assert link_updates == [album_root]
    assert finished == [(album_root, True)]
