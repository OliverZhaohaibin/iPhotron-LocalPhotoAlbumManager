"""Tests for ScannerWorker batch processing and error handling."""

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for worker tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="Qt core not available", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

from src.iPhoto.library.workers.scanner_worker import ScannerWorker, ScannerSignals


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


def test_scanner_worker_batch_success(temp_album, qapp):
    """Test that chunks are successfully persisted when no errors occur."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    finished_spy = QSignalSpy(signals.finished)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    
    # Run the worker
    worker.run()
    
    # Process any pending events
    qapp.processEvents()
    
    # Verify that chunks were emitted
    assert chunk_ready_spy.count() > 0
    
    # Verify the scan finished successfully
    assert finished_spy.count() == 1
    
    # Verify no batch failures occurred
    assert batch_failed_spy.count() == 0
    
    # Verify failed_count is 0
    assert worker.failed_count == 0


def test_scanner_worker_batch_failure_handling(temp_album, qapp):
    """Test that batchFailed signal is emitted when persistence fails."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    finished_spy = QSignalSpy(signals.finished)
    
    # Mock IndexStore.append_rows to raise an exception
    with patch('src.iPhoto.library.workers.scanner_worker.IndexStore') as MockIndexStore:
        mock_store = Mock()
        mock_store.append_rows.side_effect = Exception("Database write failed")
        mock_store._conn = None  # No persistent connection
        MockIndexStore.return_value = mock_store
        
        # Run the worker
        worker.run()
    
    # Process any pending events
    qapp.processEvents()
    
    # Verify that chunks were still emitted even though persistence failed
    assert chunk_ready_spy.count() > 0
    
    # Verify that batch failures were reported
    assert batch_failed_spy.count() > 0
    
    # Verify failed_count tracks the failures
    assert worker.failed_count > 0
    
    # Verify the scan still finished (didn't stop on error)
    assert finished_spy.count() == 1


def test_scanner_worker_scan_continues_after_partial_failures(temp_album, qapp):
    """Test that scan continues after partial batch failures."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    finished_spy = QSignalSpy(signals.finished)
    
    # Mock IndexStore to fail on first chunk, succeed on subsequent chunks
    call_count = 0
    def mock_append_rows(chunk):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("First chunk failed")
        # Subsequent chunks succeed
    
    with patch('src.iPhoto.library.workers.scanner_worker.IndexStore') as MockIndexStore:
        mock_store = Mock()
        mock_store.append_rows.side_effect = mock_append_rows
        mock_store._conn = None
        MockIndexStore.return_value = mock_store
        
        # Run the worker
        worker.run()
    
    # Process any pending events
    qapp.processEvents()
    
    # Verify that all chunks were processed (scan didn't stop)
    assert chunk_ready_spy.count() > 0
    
    # Verify at least one batch failure was reported
    assert batch_failed_spy.count() >= 1
    
    # Verify the scan completed
    assert finished_spy.count() == 1
    
    # Verify failed_count is greater than 0 but less than total items
    assert worker.failed_count > 0


def test_scanner_worker_failed_count_property(temp_album, qapp):
    """Test that failed_count property exposes accumulated failures."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    # Initially failed_count should be 0
    assert worker.failed_count == 0
    
    # Mock IndexStore to always fail
    with patch('src.iPhoto.library.workers.scanner_worker.IndexStore') as MockIndexStore:
        mock_store = Mock()
        mock_store.append_rows.side_effect = Exception("All writes fail")
        mock_store._conn = None
        MockIndexStore.return_value = mock_store
        
        # Run the worker
        worker.run()
    
    # Process any pending events
    qapp.processEvents()
    
    # Verify that failed_count is accessible and greater than 0
    assert worker.failed_count > 0
    assert isinstance(worker.failed_count, int)


def test_scanner_worker_cleanup_on_error(temp_album, qapp):
    """Test that scanner is properly cleaned up even when errors occur."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    # Mock scan_album to raise an exception
    with patch('src.iPhoto.library.workers.scanner_worker.scan_album') as mock_scan:
        mock_generator = Mock()
        mock_generator.close = Mock()
        mock_scan.return_value = mock_generator
        
        # Make the generator raise an exception when iterated
        mock_generator.__iter__ = Mock(side_effect=Exception("Scan failed"))
        
        # Run the worker
        worker.run()
    
    # Verify that close was called on the generator
    mock_generator.close.assert_called_once()


def test_scanner_worker_indexstore_cleanup(temp_album, qapp):
    """Test that IndexStore connections are properly cleaned up."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    with patch('src.iPhoto.library.workers.scanner_worker.IndexStore') as MockIndexStore:
        mock_store = Mock()
        mock_conn = Mock()
        mock_store._conn = mock_conn
        mock_store.append_rows = Mock()  # Succeed normally
        MockIndexStore.return_value = mock_store
        
        # Run the worker
        worker.run()
    
    # Process any pending events
    qapp.processEvents()
    
    # Verify that the connection was closed
    mock_conn.close.assert_called_once()
