from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
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


def test_successful_startup_scan_arms_people_and_pets_idle_gate(
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
    manager.request_startup_recognition_after_idle()

    with (
        patch.object(manager, "_arm_startup_recognition_idle_timer") as arm_idle,
        patch.object(manager._scan_thread_pool, "start") as start_pool,
    ):
        manager._on_scan_finished(worker, root, [{"rel": "pet.jpg"}])
        qapp.processEvents()

    worker.scan_service.finalize_scan_result.assert_called_once()
    arm_idle.assert_called_once_with(root, manager._recognition_generation)
    start_pool.assert_called_once()


def test_startup_recognition_idle_gate_is_invalidated_by_shutdown_generation(
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
    manager.request_startup_recognition_after_idle()

    def invalidate_recognition_generation(*_args) -> None:
        manager._recognition_generation += 1

    manager.scanFinished.connect(invalidate_recognition_generation)

    with (
        patch.object(manager, "_arm_startup_recognition_idle_timer") as arm_idle,
        patch.object(manager._scan_thread_pool, "start") as start_pool,
    ):
        manager._on_scan_finished(worker, root, [{"rel": "pet.jpg"}])
        qapp.processEvents()

    arm_idle.assert_not_called()
    start_pool.assert_called_once()


def test_idle_timeout_lazily_binds_and_starts_both_recognition_services(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    people_service = object()
    pet_service = object()
    manager._library_session = SimpleNamespace(
        library_root=root,
        people=people_service,
        pets=pet_service,
    )

    with (
        patch.object(manager, "bind_recognition_services") as bind_services,
        patch.object(manager, "activate_recognition_scans") as activate,
    ):
        manager.request_startup_recognition_after_idle()
        assert manager._startup_recognition_timer.isActive()
        assert manager._startup_recognition_timer.interval() == 1500
        manager.notify_user_activity()
        assert manager._startup_recognition_timer.isActive()
        manager._startup_recognition_timer.stop()
        manager._activate_startup_recognition_after_idle()

    bind_services.assert_called_once_with(people_service, pet_service)
    activate.assert_called_once_with()


def test_failed_startup_scan_cancels_pending_idle_activation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    worker = _startup_worker()
    worker.failed = True
    manager._current_scanner_worker = worker
    manager._live_scan_root = root
    manager.request_startup_recognition_after_idle()

    manager._on_scan_finished(worker, root, [])

    assert manager._startup_recognition_request is None
    assert not manager._startup_recognition_timer.isActive()
