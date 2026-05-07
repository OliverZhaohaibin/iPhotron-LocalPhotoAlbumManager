"""Tests for ScannerWorker batch processing and error handling."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for worker tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="Qt core not available", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

import iPhoto.library.workers.scanner_worker as scanner_worker_module
from iPhoto.application.use_cases.scan_models import (
    ScanCompletion,
    ScanMode,
    ScanPlan,
    ScanPressureLevel,
    ScanScopeKind,
)
from iPhoto.library.workers.scanner_worker import ScannerWorker, ScannerSignals


@pytest.fixture(scope="module")
def qapp():
    """Qt application instance for signal processing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture
def temp_album(tmp_path):
    """Create a temporary album with test images."""
    album = tmp_path / "TestAlbum"
    album.mkdir()
    
    # Create some test image files
    for i in range(5):
        (album / f"test_{i}.jpg").write_bytes(b"fake image data")
    
    return album


def _fake_rows(n=15):
    """Return *n* fake scan rows."""
    return [{"rel": f"test_{i}.jpg", "mime": "image/jpeg"} for i in range(n)]


def _patch_scan_and_repo(fake_rows, repo_side_effect=None):
    """Context manager that patches scan_album and get_global_repository.
    
    ``fake_rows`` is yielded one-by-one from the fake scanner.
    ``repo_side_effect`` is assigned to ``store.merge_scan_rows.side_effect``.
    """
    mock_store = Mock()
    mock_store.merge_scan_rows.side_effect = lambda chunk, **_kwargs: list(chunk)
    if repo_side_effect is not None:
        mock_store.merge_scan_rows.side_effect = repo_side_effect

    def fake_scan_album(*_args, **_kwargs):
        yield from fake_rows

    return (
        patch(
            'iPhoto.infrastructure.services.filesystem_media_scanner.scan_album',
            side_effect=fake_scan_album,
        ),
        patch('iPhoto.bootstrap.library_scan_service.get_global_repository', return_value=mock_store),
        mock_store,
    )


def test_scanner_worker_batch_success(temp_album, qapp):
    """Test that chunks are successfully persisted when no errors occur."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    finished_spy = QSignalSpy(signals.finished)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    
    rows = _fake_rows(15)
    p_scan, p_repo, mock_store = _patch_scan_and_repo(rows)

    with p_scan, p_repo:
        worker.run()
    
    qapp.processEvents()
    
    assert chunk_ready_spy.count() > 0
    assert finished_spy.count() == 1
    assert batch_failed_spy.count() == 0
    assert worker.failed_count == 0


def test_scanner_worker_uses_injected_scan_service(temp_album, qapp):
    """The worker should be a Qt transport around the session scan service."""

    class FakeScanService:
        def __init__(self) -> None:
            self.calls = []
            self.plans = []

        def plan_scan(self, root: Path, **kwargs):
            self.plans.append((Path(root), kwargs))
            return ScanPlan(
                root=Path(root),
                include=("*.jpg",),
                exclude=(),
                mode=ScanMode.BACKGROUND,
                scope_kind=ScanScopeKind.ALBUM_SUBTREE,
                scan_id="scan-1",
                persist_chunks=True,
                collect_rows=False,
                safe_mode=False,
                estimated_asset_count=None,
                pressure_level=ScanPressureLevel.NORMAL,
                degrade_reason=None,
                generate_micro_thumbnails=True,
                allow_face_scan=True,
                defer_live_pairing=False,
            )

        def start_scan(self, plan: ScanPlan, **kwargs):
            self.calls.append((plan, kwargs))
            kwargs["chunk_callback"]([{"rel": "test_0.jpg"}])
            return SimpleNamespace(
                failed_count=0,
                processed_count=1,
                cancelled=False,
                rows=[],
            )

    signals = ScannerSignals()
    service = FakeScanService()
    worker = ScannerWorker(
        temp_album,
        ["*.jpg"],
        [],
        signals,
        scan_service=service,
    )
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    finished_spy = QSignalSpy(signals.finished)

    worker.run()
    qapp.processEvents()

    assert service.calls
    plan, kwargs = service.calls[0]
    assert plan.root == temp_album
    assert plan.mode == ScanMode.BACKGROUND
    assert chunk_ready_spy.count() == 1
    assert finished_spy.count() == 1
    emitted_completion = finished_spy.at(0)[0]
    assert isinstance(emitted_completion, ScanCompletion)
    assert emitted_completion.scan_id == "scan-1"
    assert emitted_completion.processed_count == 1


def test_scanner_worker_batch_failure_handling(temp_album, qapp):
    """Test that batchFailed signal is emitted when persistence fails."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    finished_spy = QSignalSpy(signals.finished)
    
    rows = _fake_rows(15)
    p_scan, p_repo, mock_store = _patch_scan_and_repo(
        rows, repo_side_effect=Exception("Database write failed"),
    )

    with p_scan, p_repo:
        worker.run()
    
    qapp.processEvents()
    
    assert chunk_ready_spy.count() == 0
    assert batch_failed_spy.count() > 0
    assert worker.failed_count > 0
    assert finished_spy.count() == 1


def test_scanner_worker_degrades_to_constrained_mode_on_memory_warning(
    temp_album,
    qapp,
    monkeypatch,
):
    class FakeMemoryMonitor:
        def __init__(self) -> None:
            self._warning_callbacks = []
            self._warning_emitted = False

        def add_warning_callback(self, cb) -> None:
            self._warning_callbacks.append(cb)

        def add_critical_callback(self, _cb) -> None:
            return None

        def check(self):
            if not self._warning_emitted:
                self._warning_emitted = True
                for callback in list(self._warning_callbacks):
                    callback(object())
            return None

    class FakeScanService:
        def __init__(self) -> None:
            self.marked = []

        def plan_scan(self, root: Path, **_kwargs):
            return ScanPlan(
                root=Path(root),
                include=("*.jpg",),
                exclude=(),
                mode=ScanMode.BACKGROUND,
                scope_kind=ScanScopeKind.ALBUM_SUBTREE,
                scan_id="scan-1",
                persist_chunks=True,
                collect_rows=False,
                safe_mode=False,
                estimated_asset_count=None,
                pressure_level=ScanPressureLevel.NORMAL,
                degrade_reason=None,
                generate_micro_thumbnails=True,
                allow_face_scan=True,
                defer_live_pairing=False,
            )

        def mark_scan_constrained(self, scan_id: str, **kwargs) -> None:
            self.marked.append((scan_id, kwargs))

        def start_scan(self, plan: ScanPlan, **kwargs):
            kwargs["progress_callback"](1, 1)
            kwargs["chunk_callback"]([{"rel": "test_0.jpg"}])
            return SimpleNamespace(
                failed_count=0,
                processed_count=1,
                cancelled=False,
                rows=[],
            )

    monkeypatch.setattr(scanner_worker_module, "MemoryMonitor", FakeMemoryMonitor)

    signals = ScannerSignals()
    service = FakeScanService()
    worker = ScannerWorker(
        temp_album,
        ["*.jpg"],
        [],
        signals,
        scan_service=service,
    )
    finished_spy = QSignalSpy(signals.finished)

    worker.run()
    qapp.processEvents()

    completion = finished_spy.at(0)[0]
    assert isinstance(completion, ScanCompletion)
    assert completion.pressure_level == ScanPressureLevel.CONSTRAINED
    assert completion.defer_live_pairing is True
    assert completion.deferred_face_scan is True
    assert service.marked == [
        (
            "scan-1",
            {
                "reason": "memory warning",
                "defer_live_pairing": True,
                "defer_face_scan": True,
            },
        )
    ]


def test_scanner_worker_scan_continues_after_partial_failures(temp_album, qapp):
    """Test that scan continues after partial batch failures."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    finished_spy = QSignalSpy(signals.finished)
    
    call_count = 0
    def mock_merge_scan_rows(chunk, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("First chunk failed")
        return list(chunk)

    rows = _fake_rows(25)  # enough for multiple chunks
    p_scan, p_repo, mock_store = _patch_scan_and_repo(
        rows, repo_side_effect=mock_merge_scan_rows,
    )

    with p_scan, p_repo:
        worker.run()
    
    qapp.processEvents()
    
    assert chunk_ready_spy.count() > 0
    assert batch_failed_spy.count() >= 1
    assert finished_spy.count() == 1
    assert worker.failed_count > 0
    emitted_completion = finished_spy.at(0)[0]
    assert isinstance(emitted_completion, ScanCompletion)
    assert emitted_completion.success is False
    assert emitted_completion.phase.name == "FAILED"


def test_scanner_worker_failed_count_property(temp_album, qapp):
    """Test that failed_count property exposes accumulated failures."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    assert worker.failed_count == 0
    
    rows = _fake_rows(15)
    p_scan, p_repo, mock_store = _patch_scan_and_repo(
        rows, repo_side_effect=Exception("All writes fail"),
    )

    with p_scan, p_repo:
        worker.run()
    
    qapp.processEvents()
    
    assert worker.failed_count > 0
    assert isinstance(worker.failed_count, int)


def test_scanner_worker_cleanup_on_error(temp_album, qapp):
    """Test that scanner is properly cleaned up even when errors occur."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    with patch('iPhoto.infrastructure.services.filesystem_media_scanner.scan_album') as mock_scan, \
         patch('iPhoto.bootstrap.library_scan_service.get_global_repository'):
        mock_generator = Mock()
        mock_generator.close = Mock()
        mock_scan.return_value = mock_generator
        
        mock_generator.__iter__ = Mock(side_effect=Exception("Scan failed"))
        
        worker.run()
    
    mock_generator.close.assert_called_once()


def test_scanner_worker_global_repo_not_closed(temp_album, qapp):
    """The global repository singleton must NOT be closed by the worker."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    rows = _fake_rows(5)
    p_scan, p_repo, mock_store = _patch_scan_and_repo(rows)
    mock_store.close = Mock()

    with p_scan, p_repo:
        worker.run()
    
    qapp.processEvents()
    
    mock_store.close.assert_not_called()
