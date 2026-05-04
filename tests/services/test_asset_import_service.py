"""Unit tests for :mod:`iPhoto.gui.services.asset_import_service`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for asset import service tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets are required for asset import service tests",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication

import iPhoto.gui.services.asset_import_service as asset_import_module
from iPhoto.gui.services.asset_import_service import AssetImportService
from iPhoto.gui.ui.tasks.import_worker import ImportWorker


@pytest.fixture()
def qapp() -> QApplication:
    """Ensure a QApplication instance exists for QObject-based services."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _create_service(
    *,
    task_manager,
    current_album_root,
    refresh,
    metadata_service,
    library_manager=None,
) -> AssetImportService:
    """Create a service instance suitable for isolated unit tests."""

    return AssetImportService(
        task_manager=task_manager,
        current_album_root=current_album_root,
        refresh_callback=refresh,
        metadata_service=metadata_service,
        library_manager_getter=(lambda: library_manager),
    )


class _FakeLibraryManager:
    def __init__(
        self,
        root: Path,
        *,
        scan_service: object | None = None,
        lifecycle_service: object | None = None,
    ) -> None:
        self._root = Path(root)
        self.scan_service = scan_service if scan_service is not None else object()
        self.asset_lifecycle_service = (
            lifecycle_service if lifecycle_service is not None else object()
        )

    def root(self) -> Path:
        return self._root


def test_import_files_submits_background_task(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Valid sources should generate a unique background task submission."""

    album_root = tmp_path / "Album"
    album_root.mkdir()
    asset = tmp_path / "photo.jpg"
    asset.write_bytes(b"data")

    task_manager = mocker.MagicMock()
    metadata_service = mocker.MagicMock()
    refresh = mocker.MagicMock()
    library_manager = _FakeLibraryManager(album_root)

    service = _create_service(
        task_manager=task_manager,
        current_album_root=lambda: album_root,
        refresh=refresh,
        metadata_service=metadata_service,
        library_manager=library_manager,
    )

    service.import_files([asset])

    # The task manager should receive exactly one submission with a worker instance.
    assert task_manager.submit_task.call_count == 1
    kwargs = task_manager.submit_task.call_args.kwargs
    assert kwargs["task_id"].startswith(f"import:{album_root}:")
    worker = kwargs["worker"]
    assert isinstance(worker, ImportWorker)


def test_import_files_falls_back_to_standalone_services_when_library_is_unbound(
    mocker,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Open Album workflows should still queue imports without a bound library."""

    album_root = tmp_path / "Album"
    album_root.mkdir()
    asset = tmp_path / "photo.jpg"
    asset.write_bytes(b"data")

    task_manager = mocker.MagicMock()
    metadata_service = mocker.MagicMock()
    refresh = mocker.MagicMock()
    fallback_scan = object()
    fallback_lifecycle = object()
    scan_roots: list[Path] = []
    lifecycle_calls: list[tuple[Path, object]] = []

    def _create_scan(root: Path):
        scan_roots.append(Path(root))
        return fallback_scan

    def _create_lifecycle(root: Path, *, scan_service=None):
        lifecycle_calls.append((Path(root), scan_service))
        return fallback_lifecycle

    monkeypatch.setattr(
        asset_import_module,
        "create_standalone_scan_service",
        _create_scan,
    )
    monkeypatch.setattr(
        asset_import_module,
        "create_standalone_asset_lifecycle_service",
        _create_lifecycle,
    )

    service = _create_service(
        task_manager=task_manager,
        current_album_root=lambda: album_root,
        refresh=refresh,
        metadata_service=metadata_service,
        library_manager=None,
    )
    errors: list[str] = []
    service.errorRaised.connect(errors.append)

    service.import_files([asset])

    assert task_manager.submit_task.call_count == 1
    kwargs = task_manager.submit_task.call_args.kwargs
    worker = kwargs["worker"]
    assert isinstance(worker, ImportWorker)
    assert scan_roots == [album_root]
    assert lifecycle_calls == [(album_root, fallback_scan)]
    assert worker._scan_service is fallback_scan
    assert worker._asset_lifecycle_service is fallback_lifecycle
    assert errors == []


def test_import_files_uses_standalone_services_for_album_outside_bound_library(
    mocker,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """A standalone album should not write imports into the bound library index."""

    library_root = tmp_path / "Library"
    album_root = tmp_path / "StandaloneAlbum"
    library_root.mkdir()
    album_root.mkdir()
    asset = tmp_path / "photo.jpg"
    asset.write_bytes(b"data")

    task_manager = mocker.MagicMock()
    metadata_service = mocker.MagicMock()
    refresh = mocker.MagicMock()
    fallback_scan = object()
    fallback_lifecycle = object()
    scan_roots: list[Path] = []
    lifecycle_calls: list[tuple[Path, object]] = []

    def _create_scan(root: Path):
        scan_roots.append(Path(root))
        return fallback_scan

    def _create_lifecycle(root: Path, *, scan_service=None):
        lifecycle_calls.append((Path(root), scan_service))
        return fallback_lifecycle

    monkeypatch.setattr(
        asset_import_module,
        "create_standalone_scan_service",
        _create_scan,
    )
    monkeypatch.setattr(
        asset_import_module,
        "create_standalone_asset_lifecycle_service",
        _create_lifecycle,
    )

    service = _create_service(
        task_manager=task_manager,
        current_album_root=lambda: album_root,
        refresh=refresh,
        metadata_service=metadata_service,
        library_manager=_FakeLibraryManager(library_root),
    )

    service.import_files([asset])

    assert task_manager.submit_task.call_count == 1
    worker = task_manager.submit_task.call_args.kwargs["worker"]
    assert worker._library_root == album_root
    assert scan_roots == [album_root]
    assert lifecycle_calls == [(album_root, fallback_scan)]
    assert worker._scan_service is fallback_scan
    assert worker._asset_lifecycle_service is fallback_lifecycle


def test_handle_import_finished_updates_models(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Finalising an import should refresh models and optionally mark featured items."""

    album_root = tmp_path / "Album"
    album_root.mkdir()
    imported = [album_root / "photo.jpg"]

    task_manager = mocker.MagicMock()
    metadata_service = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _create_service(
        task_manager=task_manager,
        current_album_root=lambda: album_root,
        refresh=refresh,
        metadata_service=metadata_service,
    )

    results: list[tuple[Path, bool, str]] = []
    service.importFinished.connect(lambda root, success, message: results.append((root, success, message)))

    # Simulate the ``on_finished`` callback provided to ``submit_task``.
    service._handle_import_finished(album_root, imported, True, True)

    assert results == [(album_root, True, "Imported 1 file.")]
    refresh.assert_called_once_with(album_root)
    metadata_service.ensure_featured_entries.assert_called_once_with(album_root, imported)


def test_normalise_sources_filters_invalid_entries(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Only real files should pass through the normalisation step."""

    album_root = tmp_path / "Album"
    album_root.mkdir()
    valid = tmp_path / "photo.jpg"
    valid.write_bytes(b"data")
    missing = tmp_path / "missing.jpg"

    task_manager = mocker.MagicMock()
    metadata_service = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _create_service(
        task_manager=task_manager,
        current_album_root=lambda: album_root,
        refresh=refresh,
        metadata_service=metadata_service,
    )

    normalised = service._normalise_sources([valid, missing, valid])

    assert normalised == [valid.resolve()]
