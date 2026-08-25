from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for library tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtTest", reason="Qt test helpers not available", exc_type=ImportError)

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from iPhoto.bootstrap.library_session import LibrarySession
from iPhoto.errors import AlbumDepthError, AlbumOperationError, LibraryUnavailableError
from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.pets.pipeline import PET_DETECTOR_PIPELINE_VERSION


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _write_manifest(path: Path, title: str) -> None:
    payload = {
        "schema": "iPhoto/album@1",
        "title": title,
        "filters": {},
    }
    manifest = path / ".iphoto.album.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")


def test_bind_and_scan_tree(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    manager = LibraryRuntimeController()
    spy = QSignalSpy(manager.treeUpdated)
    with pytest.raises(LibraryUnavailableError):
        manager.bind_path(root)
    album = root / "Trip"
    child = album / "Day1"
    child.mkdir(parents=True)
    _write_manifest(album, "Summer Trip")
    manager.bind_path(root)
    qapp.processEvents()
    assert spy.count() >= 1
    albums = manager.list_albums()
    assert len(albums) == 1
    assert albums[0].title == "Summer Trip"
    children = manager.list_children(albums[0])
    assert len(children) == 1
    assert children[0].level == 2
    assert children[0].title == "Day1"


def test_bind_path_relays_people_snapshot_events(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)

    snapshot_spy = QSignalSpy(manager.peopleSnapshotCommitted)
    index_spy = QSignalSpy(manager.peopleIndexUpdated)
    coordinator = manager._people_index_coordinator
    assert coordinator is not None

    event = object()
    coordinator.snapshotCommitted.emit(event)
    qapp.processEvents()

    assert snapshot_spy.count() == 1
    assert snapshot_spy.at(0)[0] is event
    assert index_spy.count() == 1


def test_people_snapshot_reconciles_people_priority_pet_detections(
    qapp: QApplication,
) -> None:
    manager = LibraryRuntimeController()
    pet_service = SimpleNamespace(reconcile_people_overlaps=Mock())
    manager._pet_service = pet_service
    event = SimpleNamespace(changed_asset_ids=("asset-a", "asset-b"))

    manager._on_people_snapshot_committed(event)
    qapp.processEvents()

    pet_service.reconcile_people_overlaps.assert_called_once_with(("asset-a", "asset-b"))


def test_bind_path_rebinds_people_snapshot_events_for_prebound_session(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    session = LibrarySession(root)
    manager.bind_library_session(session)

    manager.bind_path(root)
    qapp.processEvents()

    snapshot_spy = QSignalSpy(manager.peopleSnapshotCommitted)
    index_spy = QSignalSpy(manager.peopleIndexUpdated)
    coordinator = manager._people_index_coordinator
    assert coordinator is not None

    event = object()
    coordinator.snapshotCommitted.emit(event)
    qapp.processEvents()

    assert snapshot_spy.count() == 1
    assert snapshot_spy.at(0)[0] is event
    assert index_spy.count() == 1


def test_bind_path_from_session_defers_snapshot_coordinator_until_scan_activation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    session = LibrarySession(root)
    manager.bind_library_session(session)

    manager.bind_path_from_session(root)
    qapp.processEvents()

    assert manager._people_index_coordinator is None

    manager.activate_recognition_scans()
    qapp.processEvents()

    snapshot_spy = QSignalSpy(manager.peopleSnapshotCommitted)
    index_spy = QSignalSpy(manager.peopleIndexUpdated)
    coordinator = manager._people_index_coordinator
    assert coordinator is not None
    event = object()
    coordinator.snapshotCommitted.emit(event)
    qapp.processEvents()

    assert snapshot_spy.count() == 1
    assert snapshot_spy.at(0)[0] is event
    assert index_spy.count() == 1


def test_bind_path_auto_binds_headless_library_session(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()

    manager.bind_path(root)
    qapp.processEvents()

    assert manager.library_session is not None
    assert manager.library_session.library_root == root
    assert manager.scan_service is not None
    assert manager.asset_query_service is not None
    assert manager.asset_lifecycle_service is not None
    assert manager.location_service is not None


def test_create_and_rename_album(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    created = manager.create_album("Paris")
    assert created.level == 1
    assert (created.path / ".iphoto.album.json").exists()
    sub = manager.create_subalbum(created, "Day0")
    assert sub.level == 2
    with pytest.raises(AlbumDepthError):
        manager.create_subalbum(sub, "TooDeep")
    rename_spy = QSignalSpy(manager.albumRenamed)
    old_sub_path = sub.path
    with patch.object(manager, "stop_scanning", wraps=manager.stop_scanning) as stop_scanning:
        manager.rename_album(sub, "Arrival")
    qapp.processEvents()
    stop_scanning.assert_called_once_with()
    assert rename_spy.count() == 1
    assert rename_spy.at(0) == [old_sub_path, created.path / "Arrival"]
    refreshed_parent = next(
        node for node in manager.list_albums() if node.path == created.path
    )
    refreshed_children = manager.list_children(refreshed_parent)
    assert any(child.title == "Arrival" for child in refreshed_children)
    manifest_path = next(
        child.path / ".iphoto.album.json"
        for child in refreshed_children
        if child.title == "Arrival"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["title"] == "Arrival"


@pytest.mark.parametrize("reserved_name", [".iPhoto", ".iphoto", ".IPHOTO", ".Trash", "exported"])
def test_reserved_album_names_are_rejected_for_create_and_rename(
    tmp_path: Path, qapp: QApplication, reserved_name: str
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)

    created = manager.create_album("Trips")
    child = manager.create_subalbum(created, "Day1")

    with pytest.raises(AlbumOperationError, match="reserved for internal use"):
        manager.create_album(reserved_name)
    with pytest.raises(AlbumOperationError, match="reserved for internal use"):
        manager.create_subalbum(created, reserved_name)
    with pytest.raises(AlbumOperationError, match="reserved for internal use"):
        manager.rename_album(created, reserved_name)
    with pytest.raises(AlbumOperationError, match="reserved for internal use"):
        manager.rename_album(child, reserved_name)

    qapp.processEvents()

    albums = manager.list_albums()
    assert any(node.path == created.path and node.title == "Trips" for node in albums)
    refreshed_parent = next(node for node in albums if node.path == created.path)
    refreshed_children = manager.list_children(refreshed_parent)
    assert any(kid.path == child.path and kid.title == "Day1" for kid in refreshed_children)


@pytest.mark.parametrize("internal_name", [".iPhoto", ".iphoto", ".IPHOTO"])
def test_work_dir_case_variants_are_hidden_from_album_tree(
    tmp_path: Path, qapp: QApplication, internal_name: str
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    visible = root / "Trips"
    visible.mkdir()
    _write_manifest(visible, "Trips")
    internal = root / internal_name
    internal.mkdir()
    _write_manifest(internal, f"Internal {internal_name}")

    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    albums = manager.list_albums()
    assert [node.title for node in albums] == ["Trips"]


def test_ensure_manifest_generates_defaults(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    album_dir = root / "NoManifest"
    album_dir.mkdir(parents=True)
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    node = next(node for node in manager.list_albums() if node.path == album_dir)
    manifest_path = manager.ensure_manifest(node)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["title"] == "NoManifest"
    assert data["schema"] == "iPhoto/album@1"


def test_scan_finished_skips_prune_when_worker_failed(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)

    class _Worker:
        cancelled = False
        failed = True
        scan_service = Mock()

    worker = _Worker()
    spy = QSignalSpy(manager.scanFinished)
    manager._current_scanner_worker = worker
    manager._live_scan_root = root

    with patch.object(manager._scan_thread_pool, "start") as start_mock:
        manager._on_scan_finished(worker, root, [])
        qapp.processEvents()

    _Worker.scan_service.finalize_scan_result.assert_not_called()
    start_mock.assert_not_called()
    assert spy.count() == 1
    assert spy.at(0)[1] is False


def test_scan_finished_skips_prune_when_worker_cancelled(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)

    class _Worker:
        cancelled = True
        failed = False
        scan_service = Mock()

    worker = _Worker()
    spy = QSignalSpy(manager.scanFinished)
    manager._current_scanner_worker = worker
    manager._live_scan_root = root

    with patch.object(manager._scan_thread_pool, "start") as start_mock:
        manager._on_scan_finished(worker, root, [])
        qapp.processEvents()

    _Worker.scan_service.finalize_scan_result.assert_not_called()
    start_mock.assert_not_called()
    assert spy.count() == 1
    assert spy.at(0)[1] is False


def test_stop_scanning_still_reports_cancelled_completion(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)

    class _Worker:
        cancelled = False
        failed = False
        scan_service = Mock()

        def cancel(self) -> None:
            self.cancelled = True

    worker = _Worker()
    spy = QSignalSpy(manager.scanFinished)
    manager._current_scanner_worker = worker
    manager._live_scan_root = root

    manager.stop_scanning()
    manager._on_scan_finished(worker, root, [])
    qapp.processEvents()

    _Worker.scan_service.finalize_scan_result.assert_not_called()
    assert spy.count() == 1
    assert spy.at(0)[1] is False


def test_shutdown_cancels_and_waits_for_scan_workers(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()

    class _Worker:
        cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    worker = _Worker()
    face_scanner = Mock()
    face_scanner.isRunning.return_value = False
    pet_scanner = Mock()
    pet_scanner.isRunning.return_value = False
    manager._current_scanner_worker = worker
    manager._current_face_scanner = face_scanner
    manager._current_pet_scanner = pet_scanner
    manager._live_scan_root = root

    with patch.object(
        manager._scan_thread_pool,
        "waitForDone",
        return_value=True,
    ) as wait_for_done:
        manager.shutdown()

    assert worker.cancelled is True
    face_scanner.cancel.assert_called_once_with()
    face_scanner.wait.assert_called_once_with(2000)
    pet_scanner.cancel.assert_called_once_with()
    pet_scanner.wait.assert_called_once_with(2000)
    wait_for_done.assert_called_once_with(2000)


def test_scan_finished_ignores_stale_worker_result(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)

    current_worker = Mock(cancelled=False, failed=False)
    stale_worker = Mock(cancelled=False, failed=False)
    manager._current_scanner_worker = current_worker
    manager._live_scan_root = root
    spy = QSignalSpy(manager.scanFinished)

    manager._on_scan_finished(stale_worker, root, [])
    qapp.processEvents()

    assert manager._current_scanner_worker is current_worker
    assert spy.count() == 0


def test_scan_finished_reports_failure_when_finalization_fails(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    scan_service = Mock()
    scan_service.finalize_scan_result.side_effect = RuntimeError("finalize failed")

    worker = Mock(cancelled=False, failed=False, scan_service=scan_service)
    spy = QSignalSpy(manager.scanFinished)
    manager._current_scanner_worker = worker
    manager._live_scan_root = root

    with patch.object(manager._scan_thread_pool, "start") as start_mock:
        manager._on_scan_finished(worker, root, [{"rel": "a.jpg"}])
        qapp.processEvents()

    scan_service.finalize_scan_result.assert_called_once()
    start_mock.assert_not_called()
    assert spy.count() == 1
    assert spy.at(0)[1] is False


def test_scan_request_is_queued_until_active_scan_finishes(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    first = root / "First"
    second = root / "Second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    scan_service = Mock()

    worker = Mock(cancelled=False, failed=False, scan_service=scan_service)
    manager._current_scanner_worker = worker
    manager._live_scan_root = first

    manager.start_scanning(second, ["*.jpg"], ["*.mov"])
    assert manager._deferred_scan_queue == [(second, ["*.jpg"], ["*.mov"], False)]

    with patch.object(manager, "start_scanning") as start_mock:
        manager._on_scan_finished(worker, first, [{"rel": "a.jpg"}])
        qapp.processEvents()

    scan_service.finalize_scan_result.assert_called_once()
    start_mock.assert_called_once_with(second, ["*.jpg"], ["*.mov"], startup=False)


def test_deferred_scan_waits_for_face_scan_to_finish(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    first = root / "First"
    second = root / "Second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    scan_service = Mock()

    worker = Mock(cancelled=False, failed=False, scan_service=scan_service)
    face_scanner = Mock()
    face_scanner.isRunning.return_value = True
    manager._current_scanner_worker = worker
    manager._current_face_scanner = face_scanner
    manager._live_scan_root = first

    manager.start_scanning(second, ["*.jpg"], ["*.mov"])

    with patch.object(manager, "start_scanning") as start_mock:
        manager._on_scan_finished(worker, first, [{"rel": "a.jpg"}])
        qapp.processEvents()

        face_scanner.finish_input.assert_called_once_with()
        start_mock.assert_not_called()
        face_scanner.isRunning.return_value = False
        manager._on_face_scan_finished(face_scanner)

    start_mock.assert_called_once_with(second, ["*.jpg"], ["*.mov"], startup=False)


def test_regular_scan_starts_ai_workers_immediately(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    manager.bind_scan_service(Mock())

    with (
        patch.object(manager._scan_thread_pool, "start") as start_thread,
        patch.object(manager, "_start_ai_scan_workers") as start_ai,
    ):
        manager.start_scanning(root, ["*.jpg"], ["*.mov"])

    start_ai.assert_called_once_with(root)
    start_thread.assert_called_once()


def test_startup_scan_leaves_ai_workers_for_first_recognition_use(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    scan_service = Mock()
    manager.bind_scan_service(scan_service)

    with (
        patch.object(manager._scan_thread_pool, "start") as start_thread,
        patch.object(manager, "_start_ai_scan_workers") as start_ai,
        patch("iPhoto.library.scan_coordinator.mark") as profile_mark,
    ):
        manager.start_scanning(root, ["*.jpg"], ["*.mov"], startup=True)

        worker = manager._current_scanner_worker
        assert worker is not None
        assert getattr(worker, "_defer_ai_workers_until_scan_finished") is True
        start_ai.assert_not_called()
        profile_mark.assert_called_once_with("startup_metadata_scan.started", root=root)
        start_thread.assert_called_once_with(worker)

        manager._on_scan_finished(worker, root, [])

    scan_service.finalize_scan_result.assert_called_once_with(
        root,
        [],
        pair_live=False,
        preserve_modified_after_ms=None,
        current_scan_job_id=None,
    )
    start_ai.assert_not_called()


def test_recognition_activation_binds_services_and_starts_finite_ai_scan(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    people_service = Mock()
    people_service.coordinator = None
    pet_service = Mock()
    pet_service.coordinator = None

    with patch.object(manager, "_start_ai_scan_workers") as start_ai:
        manager.activate_recognition_services(people_service, pet_service)

    assert manager.people_service is people_service
    assert manager.pet_service is pet_service
    start_ai.assert_called_once_with(root, startup=True)


def test_recognition_binding_does_not_start_ai_before_viewport_ready(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    people_service = Mock()
    people_service.coordinator = None
    pet_service = Mock()
    pet_service.coordinator = None

    with patch.object(manager, "_start_ai_scan_workers") as start_ai:
        manager.bind_recognition_services(people_service, pet_service)
        start_ai.assert_not_called()
        manager.activate_recognition_scans()

    start_ai.assert_called_once_with(root, startup=True)


def test_recognition_activation_waits_for_startup_metadata_scan(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    manager._recognition_services_root = root
    manager._people_service = Mock()
    manager._pet_service = Mock()
    startup_worker = Mock()
    startup_worker._defer_ai_workers_until_scan_finished = True
    manager._current_scanner_worker = startup_worker

    with patch.object(manager, "_start_ai_scan_workers") as start_ai:
        manager.activate_recognition_scans()

    start_ai.assert_not_called()
    assert manager._startup_recognition_request == (
        root,
        manager._recognition_generation,
    )
    assert not manager._startup_recognition_timer.isActive()


def test_upgraded_library_schedules_closed_input_pet_backfill_after_bind(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    repository = Mock()
    repository.get_scan_metadata.side_effect = lambda key: {
        "pet_backfill_required": None,
        "detector_pipeline_version": "legacy-detector",
        "detector_migration_target": None,
        "detector_migration_state": None,
    }[key]
    asset_repository = Mock()
    asset_repository.count_by_pet_status.return_value = {"done": 3}
    pet_service = Mock()
    pet_service.repository.return_value = repository
    pet_service.asset_repository = asset_repository

    with (
        patch("iPhoto.library.runtime_controller.QTimer.singleShot") as single_shot,
        patch.object(manager, "_start_pet_backfill_worker") as start_backfill,
    ):
        manager.bind_recognition_services(Mock(), pet_service)
        single_shot.assert_called_once()
        single_shot.call_args.args[1]()

    repository.set_scan_metadata_many.assert_called_once_with(
        {
            "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
            "detector_migration_state": "pending",
            "pet_backfill_required": "1",
        }
    )
    start_backfill.assert_called_once_with(root)


def test_new_library_does_not_schedule_pet_backfill_until_feature_use(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    repository = Mock()
    repository.get_scan_metadata.return_value = None
    asset_repository = Mock()
    asset_repository.count_by_pet_status.return_value = {"done": 0}
    pet_service = Mock()
    pet_service.repository.return_value = repository
    pet_service.asset_repository = asset_repository

    with patch("iPhoto.library.runtime_controller.QTimer.singleShot") as single_shot:
        manager.bind_recognition_services(Mock(), pet_service)

    single_shot.assert_not_called()
    repository.set_scan_metadata.assert_not_called()


def test_current_library_schedules_ordinary_pending_retry_drain_after_bind(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    repository = Mock()
    repository.get_scan_metadata.side_effect = lambda key: {
        "pet_backfill_required": None,
        "detector_pipeline_version": PET_DETECTOR_PIPELINE_VERSION,
        "detector_migration_target": PET_DETECTOR_PIPELINE_VERSION,
        "detector_migration_state": "complete",
    }[key]
    asset_repository = Mock()
    asset_repository.count_by_pet_status.return_value = {"pending": 2, "retry": 1}
    pet_service = Mock()
    pet_service.repository.return_value = repository
    pet_service.asset_repository = asset_repository

    with (
        patch("iPhoto.library.runtime_controller.QTimer.singleShot") as single_shot,
        patch.object(manager, "_start_pet_backfill_worker") as start_drain,
    ):
        manager.bind_recognition_services(Mock(), pet_service)
        single_shot.assert_called_once()
        single_shot.call_args.args[1]()

    start_drain.assert_called_once_with(root)
    repository.set_scan_metadata_many.assert_not_called()


def test_pet_backfill_event_does_not_block_face_activation(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    repository = Mock()
    repository.get_scan_metadata.side_effect = lambda key: {
        "pet_backfill_required": "1",
        "detector_pipeline_version": "legacy-detector",
        "detector_migration_target": None,
        "detector_migration_state": None,
    }[key]
    pet_service = Mock()
    pet_service.repository.return_value = repository
    pet_service.asset_repository = Mock()
    pet_service.asset_repository.count_by_pet_status.return_value = {"done": 1}
    pet_service.coordinator = None
    people_service = Mock()
    people_service.coordinator = None
    class _BackfillWorker:
        def cancel(self) -> None:
            return None

        def wait(self, _timeout: int = 0) -> bool:
            return True

        def isRunning(self) -> bool:
            return False

    backfill = _BackfillWorker()
    created_faces: list[object] = []

    class _FakeSignal:
        def connect(self, _callback) -> None:
            return None

    class _FaceWorker:
        def __init__(self, *_args, **_kwargs) -> None:
            self.statusChanged = _FakeSignal()
            self.finished = _FakeSignal()
            self.started = False
            self.input_closed = False
            created_faces.append(self)

        def setObjectName(self, _name: str) -> None:
            return None

        def finish_input(self) -> None:
            self.input_closed = True

        def start(self, _priority=None) -> None:
            self.started = True

        def cancel(self) -> None:
            return None

        def wait(self, _timeout: int = 0) -> bool:
            return True

        def isRunning(self) -> bool:
            return False

    monkeypatch.setitem(
        sys.modules,
        "iPhoto.library.workers.face_scan_worker",
        SimpleNamespace(FaceScanWorker=_FaceWorker),
    )
    with patch.object(
        manager,
        "_start_pet_backfill_worker",
        side_effect=lambda _root: setattr(manager, "_current_pet_scanner", backfill),
    ):
        manager.bind_recognition_services(people_service, pet_service)
        qapp.processEvents()

    assert manager._current_pet_scanner is backfill
    manager.activate_recognition_scans()

    assert len(created_faces) == 1
    assert created_faces[0].started is True
    assert created_faces[0].input_closed is True
    assert manager._current_pet_scanner is backfill
    assert manager._recognition_scans_root == root


def test_recognition_activation_retries_only_failed_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager._root = root
    manager._recognition_services_root = root
    manager._people_service = Mock(coordinator=None)
    manager._pet_service = Mock(coordinator=None)
    face_starts: list[int] = []
    pet_instances: list[object] = []

    class _FakeSignal:
        def connect(self, _callback) -> None:
            return None

    class _WorkerBase:
        def __init__(self, *_args, **_kwargs) -> None:
            self.statusChanged = _FakeSignal()
            self.finished = _FakeSignal()

        def setObjectName(self, _name: str) -> None:
            return None

        def finish_input(self) -> None:
            return None

        def cancel(self) -> None:
            return None

        def wait(self, _timeout: int = 0) -> bool:
            return True

        def isRunning(self) -> bool:
            return False

    class _FaceWorker(_WorkerBase):
        def start(self, _priority=None) -> None:
            face_starts.append(1)

    class _PetWorker(_WorkerBase):
        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__(*_args, **_kwargs)
            pet_instances.append(self)

        def start(self, _priority=None) -> None:
            if len(pet_instances) == 1:
                raise RuntimeError("injected Pet start failure")

    monkeypatch.setitem(
        sys.modules,
        "iPhoto.library.workers.face_scan_worker",
        SimpleNamespace(FaceScanWorker=_FaceWorker),
    )
    monkeypatch.setitem(
        sys.modules,
        "iPhoto.library.workers.pet_scan_worker",
        SimpleNamespace(PetScanWorker=_PetWorker),
    )

    manager.activate_recognition_scans()
    assert manager._recognition_scans_root is None
    assert len(face_starts) == 1
    assert len(pet_instances) == 1

    manager.activate_recognition_scans()
    assert manager._recognition_scans_root == root
    assert len(face_starts) == 1
    assert len(pet_instances) == 2


def test_library_switch_retires_pet_worker_and_rejects_late_status(
    qapp: QApplication,
) -> None:
    manager = LibraryRuntimeController()
    worker = Mock()
    worker._recognition_generation_token = manager._recognition_generation
    manager._current_pet_scanner = worker
    old_generation = manager._recognition_generation

    manager.stop_scanning(wait=False)

    worker.cancel.assert_called_once_with()
    worker.wait.assert_not_called()
    assert manager._recognition_generation == old_generation + 1
    assert worker in manager._retiring_recognition_workers

    manager._on_recognition_worker_status(worker, "pet", "late old-library status")
    assert manager._pet_scan_status_message is None

    manager._on_pet_scan_finished(worker)
    assert worker not in manager._retiring_recognition_workers


def test_map_activation_binds_location_runtime_and_interaction_services() -> None:
    manager = LibraryRuntimeController.__new__(LibraryRuntimeController)
    manager.bind_location_service = Mock()
    manager.bind_map_runtime = Mock()
    manager.bind_map_interaction_service = Mock()
    location_service = object()
    map_runtime = object()
    interaction_service = object()

    manager.activate_map_services(
        location_service,
        map_runtime,
        interaction_service,
    )

    manager.bind_location_service.assert_called_once_with(location_service)
    manager.bind_map_runtime.assert_called_once_with(map_runtime)
    manager.bind_map_interaction_service.assert_called_once_with(interaction_service)


def test_session_binding_does_not_materialize_location_before_map_use(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    session = LibrarySession(root)

    manager.bind_library_session(session)

    assert vars(session)["locations"] is None
    assert manager.location_service is None


def test_startup_ai_workers_close_input_after_metadata_scan(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    created: list[object] = []
    runtime_prepared: list[str] = []

    class _FakeSignal:
        def connect(self, _callback) -> None:
            return None

    class _FakeAiWorker:
        def __init__(self, *_args, **_kwargs) -> None:
            if not created:
                assert runtime_prepared == ["cv2"]
            self.statusChanged = _FakeSignal()
            self.finished = _FakeSignal()
            self.input_closed = False
            self.started = False
            created.append(self)

        def finish_input(self) -> None:
            self.input_closed = True

        def cancel(self) -> None:
            self.input_closed = True

        def wait(self, _timeout_ms: int) -> bool:
            return True

        def isRunning(self) -> bool:
            return False

        def start(self, _priority=None) -> None:
            self.started = True

    monkeypatch.setitem(
        sys.modules,
        "iPhoto.library.workers.face_scan_worker",
        SimpleNamespace(FaceScanWorker=_FakeAiWorker),
    )
    monkeypatch.setitem(
        sys.modules,
        "iPhoto.library.workers.pet_scan_worker",
        SimpleNamespace(PetScanWorker=_FakeAiWorker),
    )

    with (
        patch("iPhoto.library.scan_coordinator.mark") as profile_mark,
        patch(
            "iPhoto.library.scan_coordinator._prepare_face_runtime_imports",
            side_effect=lambda: runtime_prepared.append("cv2"),
        ) as prepare_runtime,
    ):
        manager._start_ai_scan_workers(root, startup=True)

    profile_mark.assert_called_once_with("startup_ai_scan.started", root=root)
    prepare_runtime.assert_called_once_with()
    assert len(created) == 2
    assert all(worker.input_closed for worker in created)
    assert all(worker.started for worker in created)
