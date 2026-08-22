from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for library tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from iPhoto.library.runtime_controller import LibraryRuntimeController


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _startup_worker() -> Mock:
    worker = Mock(cancelled=False, failed=False)
    worker.scan_service = Mock()
    worker.scan_started_at_ms = 1
    worker.scan_job_id = "startup-test"
    worker._defer_ai_workers_until_scan_finished = True
    return worker


def test_successful_startup_scan_starts_pet_backfill(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    worker = _startup_worker()
    manager._current_scanner_worker = worker
    manager._live_scan_root = root

    with (
        patch.object(manager, "_start_pet_backfill_worker") as start_pet_backfill,
        patch.object(manager._scan_thread_pool, "start") as start_pool,
    ):
        manager._on_scan_finished(worker, root, [{"rel": "pet.jpg"}])
        qapp.processEvents()

    worker.scan_service.finalize_scan_result.assert_called_once()
    start_pet_backfill.assert_called_once_with(root)
    start_pool.assert_called_once()


def test_startup_pet_backfill_is_invalidated_by_shutdown_generation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    worker = _startup_worker()
    manager._current_scanner_worker = worker
    manager._live_scan_root = root

    def invalidate_recognition_generation(*_args) -> None:
        manager._recognition_generation += 1

    manager.scanFinished.connect(invalidate_recognition_generation)

    with (
        patch.object(manager, "_start_pet_backfill_worker") as start_pet_backfill,
        patch.object(manager._scan_thread_pool, "start") as start_pool,
    ):
        manager._on_scan_finished(worker, root, [{"rel": "pet.jpg"}])
        qapp.processEvents()

    start_pet_backfill.assert_not_called()
    start_pool.assert_called_once()
