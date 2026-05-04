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


class _FakeLibraryRuntimeController:
    def __init__(
        self,
        root: Path,
        *,
        scan_service: object | None = None,
        lifecycle_service: object | None = None,
    ) -> None:
        self._root = Path(root)
        self.scan_service = (
            scan_service if scan_service is not None else _FakeScanService(root)
        )
        self.asset_lifecycle_service = (
            lifecycle_service
            if lifecycle_service is not None
            else _FakeLifecycleService(root)
        )

    def root(self) -> Path:
        return self._root


class _FakeScanService:
    def __init__(self, root: Path) -> None:
        self.library_root = Path(root)

    def prepare_album_open(self, *args, **kwargs):
        return None

    def rescan_album(self, *args, **kwargs):
        return None

    def pair_album(self, *args, **kwargs):
        return None


class _FakeLifecycleService:
    def __init__(self, root: Path) -> None:
        self.library_root = Path(root)


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
    library_manager = _FakeLibraryRuntimeController(album_root)

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


def test_import_files_requires_bound_session_when_library_is_unbound(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Open Album workflows no longer create standalone import services."""

    album_root = tmp_path / "Album"
    album_root.mkdir()
    asset = tmp_path / "photo.jpg"
    asset.write_bytes(b"data")

    task_manager = mocker.MagicMock()
    metadata_service = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _create_service(
        task_manager=task_manager,
        current_album_root=lambda: album_root,
        refresh=refresh,
        metadata_service=metadata_service,
        library_manager=None,
    )
    errors: list[str] = []
    service.errorRaised.connect(errors.append)

    with pytest.raises(RuntimeError, match="Active library session is unavailable"):
        service.import_files([asset])

    task_manager.submit_task.assert_not_called()
    assert errors == []


def test_import_files_requires_bound_session_for_album_outside_bound_library(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """A standalone album is rejected instead of building fallback services."""

    library_root = tmp_path / "Library"
    album_root = tmp_path / "StandaloneAlbum"
    library_root.mkdir()
    album_root.mkdir()
    asset = tmp_path / "photo.jpg"
    asset.write_bytes(b"data")

    task_manager = mocker.MagicMock()
    metadata_service = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _create_service(
        task_manager=task_manager,
        current_album_root=lambda: album_root,
        refresh=refresh,
        metadata_service=metadata_service,
        library_manager=_FakeLibraryRuntimeController(library_root),
    )

    with pytest.raises(RuntimeError, match="Active library session is unavailable"):
        service.import_files([asset])

    task_manager.submit_task.assert_not_called()


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
