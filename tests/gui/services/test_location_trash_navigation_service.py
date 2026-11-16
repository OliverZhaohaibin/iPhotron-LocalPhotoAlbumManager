from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI service tests")
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available")

from PySide6.QtWidgets import QApplication

from iPhoto.gui.services.location_trash_navigation_service import (
    LocationTrashNavigationService,
)


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _LocationService:
    def __init__(self) -> None:
        self.assets = ["session-asset"]
        self.calls = 0

    def list_geotagged_assets(self) -> list[str]:
        self.calls += 1
        return list(self.assets)


class _LifecycleService:
    def __init__(self) -> None:
        self.cleanup_roots: list[Path] = []

    def cleanup_deleted_index(self, trash_root: Path) -> int:
        self.cleanup_roots.append(Path(trash_root))
        return 4


class _Library:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.location_service = _LocationService()
        self.asset_lifecycle_service = _LifecycleService()
        self.legacy_location_calls = 0
        self.legacy_cleanup_calls = 0

    def root(self) -> Path:
        return self._root

    def get_geotagged_assets(self) -> list[str]:
        self.legacy_location_calls += 1
        return ["legacy-asset"]

    def cleanup_deleted_index(self) -> int:
        self.legacy_cleanup_calls += 1
        return 1


def test_location_assets_load_prefers_session_service(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    del qapp
    library = _Library(tmp_path)
    service = LocationTrashNavigationService(library_manager_getter=lambda: library)
    queued = []
    monkeypatch.setattr(service, "_thread_pool", SimpleNamespace(start=queued.append))
    loaded = []
    service.locationAssetsLoaded.connect(lambda *result: loaded.append(result))
    old_location_service = library.location_service
    library.library_session = SimpleNamespace(locations=old_location_service)
    library.location_service = None
    assert service.request_location_assets() == (1, tmp_path)
    library.location_service = _LocationService()
    library.library_session = SimpleNamespace(locations=library.location_service)
    queued[0].run()

    assert loaded == [(1, tmp_path, ["session-asset"])]
    assert old_location_service.calls == 1
    assert library.location_service.calls == 0
    assert library.legacy_location_calls == 0


def test_deleted_cleanup_prefers_session_lifecycle(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    del qapp
    library = _Library(tmp_path)
    service = LocationTrashNavigationService(library_manager_getter=lambda: library)
    trash_root = tmp_path / ".Recently Deleted"

    removed = service._cleanup_deleted_index(library, trash_root)  # noqa: SLF001

    assert removed == 4
    assert library.asset_lifecycle_service.cleanup_roots == [trash_root]
    assert library.legacy_cleanup_calls == 0
