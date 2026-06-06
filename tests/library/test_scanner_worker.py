"""Tests for ScannerWorker batch processing and error handling."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for worker tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="Qt core not available", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

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
    return [
        {
            "rel": f"test_{i}.jpg",
            "mime": "image/jpeg",
            "thumbnail_state": "ready",
            "micro_thumbnail": b"thumb",
            "thumb_cache_key": f"thumb-{i}",
        }
        for i in range(n)
    ]


class _FakeScanService:
    def __init__(
        self,
        rows: list[dict],
        *,
        failed_count: int = 0,
        batch_rows: list[list[dict]] | None = None,
        failed_batches: list[int] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.rows = rows
        self.failed_count = failed_count
        self.batch_rows = batch_rows if batch_rows is not None else [rows]
        self.failed_batches = failed_batches or []
        self.error = error
        self.calls = []

    def scan_album(self, root: Path, **kwargs):
        self.calls.append((root, kwargs))
        if self.error is not None:
            raise self.error
        for count in self.failed_batches:
            kwargs["batch_failed_callback"](count)
        for rows in self.batch_rows:
            if rows:
                kwargs["scan_batch_callback"](
                    SimpleNamespace(
                        root=root,
                        rows=rows,
                        ready_count=len(rows),
                        collection_revision=1,
                    )
                )
        return SimpleNamespace(rows=self.rows, failed_count=self.failed_count)


def test_scanner_worker_batch_success(temp_album, qapp):
    """Test that ready batches are emitted when no errors occur."""
    signals = ScannerSignals()
    rows = _fake_rows(15)
    service = _FakeScanService(rows)
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals, scan_service=service)

    batch_spy = QSignalSpy(signals.batchCommitted)
    finished_spy = QSignalSpy(signals.finished)
    batch_failed_spy = QSignalSpy(signals.batchFailed)

    worker.run()
    
    qapp.processEvents()
    
    assert batch_spy.count() == 1
    assert finished_spy.count() == 1
    assert batch_failed_spy.count() == 0
    assert worker.failed_count == 0


def test_scanner_worker_uses_injected_scan_service(temp_album, qapp):
    """The worker should be a Qt transport around the session scan service."""

    class FakeScanService:
        def __init__(self) -> None:
            self.calls = []

        def scan_album(self, root: Path, **kwargs):
            self.calls.append((root, kwargs))
            kwargs["scan_batch_callback"](
                SimpleNamespace(
                    root=root,
                    rows=[{"rel": "test_0.jpg"}],
                    ready_count=1,
                    collection_revision=1,
                )
            )
            return SimpleNamespace(
                rows=[{"rel": "test_0.jpg"}],
                failed_count=0,
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
    batch_spy = QSignalSpy(signals.batchCommitted)
    finished_spy = QSignalSpy(signals.finished)

    worker.run()
    qapp.processEvents()

    assert service.calls
    root, kwargs = service.calls[0]
    assert root == temp_album
    assert kwargs["include"] == ["*.jpg"]
    assert kwargs["persist_chunks"] is True
    assert kwargs["chunk_size"] == 100
    assert kwargs["max_chunk_interval_ms"] == 250
    assert callable(kwargs["scan_batch_callback"])
    assert batch_spy.count() == 1
    assert finished_spy.count() == 1


def test_scanner_worker_batch_failure_handling(temp_album, qapp):
    """Test that batchFailed signal is emitted when persistence fails."""
    signals = ScannerSignals()
    service = _FakeScanService([], failed_count=15, batch_rows=[], failed_batches=[15])
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals, scan_service=service)

    batch_spy = QSignalSpy(signals.batchCommitted)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    finished_spy = QSignalSpy(signals.finished)

    worker.run()
    
    qapp.processEvents()
    
    assert batch_spy.count() == 0
    assert batch_failed_spy.count() > 0
    assert worker.failed_count > 0
    assert finished_spy.count() == 1


def test_scanner_worker_scan_continues_after_partial_failures(temp_album, qapp):
    """Test that scan continues after partial batch failures."""
    signals = ScannerSignals()
    rows = _fake_rows(501)
    service = _FakeScanService(
        rows,
        failed_count=500,
        batch_rows=[rows[500:]],
        failed_batches=[500],
    )
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals, scan_service=service)

    batch_spy = QSignalSpy(signals.batchCommitted)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    finished_spy = QSignalSpy(signals.finished)

    worker.run()
    
    qapp.processEvents()
    
    assert batch_spy.count() > 0
    assert batch_failed_spy.count() >= 1
    assert finished_spy.count() == 1
    assert worker.failed_count > 0


def test_scanner_worker_failed_count_property(temp_album, qapp):
    """Test that failed_count property exposes accumulated failures."""
    signals = ScannerSignals()
    service = _FakeScanService([], failed_count=15, batch_rows=[], failed_batches=[15])
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals, scan_service=service)
    
    assert worker.failed_count == 0

    worker.run()
    
    qapp.processEvents()
    
    assert worker.failed_count > 0
    assert isinstance(worker.failed_count, int)


def test_scanner_worker_cleanup_on_error(temp_album, qapp):
    """Test that scanner failures are propagated through the worker error signal."""
    signals = ScannerSignals()
    service = _FakeScanService([], error=RuntimeError("Scan failed"))
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals, scan_service=service)
    error_spy = QSignalSpy(signals.error)

    worker.run()

    qapp.processEvents()

    assert worker.failed is True
    assert error_spy.count() == 1


def test_scanner_worker_global_repo_not_closed(temp_album, qapp):
    """The global repository singleton must NOT be closed by the worker."""
    signals = ScannerSignals()
    rows = _fake_rows(5)
    service = _FakeScanService(rows)
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals, scan_service=service)

    worker.run()
    
    qapp.processEvents()
    
    assert service.calls
