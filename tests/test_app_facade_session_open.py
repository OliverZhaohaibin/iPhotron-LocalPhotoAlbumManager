from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for facade tests",
    exc_type=ImportError,
)

from iPhoto.gui.facade import AppFacade


class FakeLibraryUpdateService:
    def __init__(self, asset_count: int = 1) -> None:
        self.asset_count = asset_count
        self.prepare_calls: list[dict] = []
        self.async_rescans: list[Path] = []

    def prepare_album_open(self, root: Path, **kwargs):
        self.prepare_calls.append({"root": root, **kwargs})
        return SimpleNamespace(
            asset_count=self.asset_count,
            should_rescan_async=self.asset_count == 0,
        )

    def rescan_album_async(self, album) -> None:
        self.async_rescans.append(album.root)


class DummyLibrary:
    def __init__(self, root: Path | None) -> None:
        self._root = root

    def root(self) -> Path | None:
        return self._root

    def is_scanning_path(self, _path: Path) -> bool:
        return False


def test_facade_open_album_uses_session_scan_service(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    update_service = FakeLibraryUpdateService(asset_count=3)
    library = DummyLibrary(library_root)
    facade = AppFacade()
    facade._library_manager = library
    facade._inject_scan_dependencies_for_tests(library_update_service=update_service)

    album = facade.open_album(album_root)

    assert album is not None
    assert album.root == album_root
    assert update_service.prepare_calls == [
        {
            "root": album_root,
            "autoscan": False,
            "hydrate_index": False,
            "sync_manifest_favorites": False,
        }
    ]
    assert update_service.async_rescans == []


def test_facade_open_album_triggers_async_rescan_for_empty_scope(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    update_service = FakeLibraryUpdateService(asset_count=0)
    library = DummyLibrary(library_root)
    facade = AppFacade()
    facade._library_manager = library
    facade._inject_scan_dependencies_for_tests(library_update_service=update_service)

    album = facade.open_album(album_root)

    assert album is not None
    assert update_service.async_rescans == [album_root]


def test_facade_open_album_syncs_manifest_favorites_when_library_root_is_unbound(
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir(parents=True)
    update_service = FakeLibraryUpdateService(asset_count=3)
    library = DummyLibrary(None)
    facade = AppFacade()
    facade._library_manager = library
    facade._inject_scan_dependencies_for_tests(library_update_service=update_service)

    album = facade.open_album(album_root)

    assert album is not None
    assert update_service.prepare_calls == [
        {
            "root": album_root,
            "autoscan": False,
            "hydrate_index": False,
            "sync_manifest_favorites": True,
        }
    ]


def test_facade_open_album_aborts_when_preparation_fails(tmp_path: Path) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir(parents=True)
    facade = AppFacade()

    class FailingLibraryUpdateService:
        def prepare_album_open(self, root: Path, **kwargs):
            raise RuntimeError(f"cannot prepare {root}")

    errors: list[str] = []
    load_finished: list[tuple[Path, bool]] = []
    facade.errorRaised.connect(errors.append)
    facade.loadFinished.connect(lambda root, success: load_finished.append((root, success)))
    facade._inject_scan_dependencies_for_tests(
        library_update_service=FailingLibraryUpdateService()
    )

    album = facade.open_album(album_root)

    assert album is None
    assert facade.current_album is None
    assert errors == [f"cannot prepare {album_root}"]
    assert load_finished == []


def test_facade_rescan_current_async_keeps_public_forwarding_shape(
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir(parents=True)
    facade = AppFacade()
    facade._current_album = SimpleNamespace(root=album_root)

    class _FakeUpdateService:
        def __init__(self) -> None:
            self.requested: list[Path] = []

        def rescan_album_async(self, album) -> None:
            self.requested.append(album.root)

    update_service = _FakeUpdateService()
    facade._inject_scan_dependencies_for_tests(library_update_service=update_service)

    facade.rescan_current_async()

    assert update_service.requested == [album_root]


def test_facade_relays_scan_batch_failures_from_library_updates(
    tmp_path: Path,
) -> None:
    facade = AppFacade()
    album_root = tmp_path / "album"
    failures: list[tuple[Path, int]] = []
    facade.scanBatchFailed.connect(lambda root, count: failures.append((root, count)))

    facade.library_updates.scanBatchFailed.emit(album_root, 2)

    assert failures == [(album_root, 2)]
