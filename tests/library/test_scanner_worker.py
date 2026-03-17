"""Tests for ScannerWorker batch processing and error handling."""

import os
from pathlib import Path
from unittest.mock import Mock, patch

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
    return [{"rel": f"test_{i}.jpg", "mime": "image/jpeg"} for i in range(n)]


def _patch_scan_and_repo(fake_rows, repo_side_effect=None):
    """Context manager that patches scan_album and get_global_repository.
    
    ``fake_rows`` is yielded one-by-one from the fake scanner.
    ``repo_side_effect`` is assigned to ``store.append_rows.side_effect``.
    """
    mock_store = Mock()
    if repo_side_effect is not None:
        mock_store.append_rows.side_effect = repo_side_effect
    else:
        mock_store.append_rows = Mock()

    def fake_scan_album(*_args, **_kwargs):
        yield from fake_rows

    return (
        patch('iPhoto.library.workers.scanner_worker.scan_album', side_effect=fake_scan_album),
        patch('iPhoto.library.workers.scanner_worker.get_global_repository', return_value=mock_store),
        patch('iPhoto.library.workers.scanner_worker.load_incremental_index_cache', return_value={}),
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
    p_scan, p_repo, p_cache, mock_store = _patch_scan_and_repo(rows)

    with p_scan, p_repo, p_cache:
        worker.run()
    
    qapp.processEvents()
    
    assert chunk_ready_spy.count() > 0
    assert finished_spy.count() == 1
    assert batch_failed_spy.count() == 0
    assert worker.failed_count == 0


def test_scanner_worker_batch_failure_handling(temp_album, qapp):
    """Test that batchFailed signal is emitted when persistence fails."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    finished_spy = QSignalSpy(signals.finished)
    
    rows = _fake_rows(15)
    p_scan, p_repo, p_cache, mock_store = _patch_scan_and_repo(
        rows, repo_side_effect=Exception("Database write failed"),
    )

    with p_scan, p_repo, p_cache:
        worker.run()
    
    qapp.processEvents()
    
    assert chunk_ready_spy.count() > 0
    assert batch_failed_spy.count() > 0
    assert worker.failed_count > 0
    assert finished_spy.count() == 1


def test_scanner_worker_scan_continues_after_partial_failures(temp_album, qapp):
    """Test that scan continues after partial batch failures."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    chunk_ready_spy = QSignalSpy(signals.chunkReady)
    batch_failed_spy = QSignalSpy(signals.batchFailed)
    finished_spy = QSignalSpy(signals.finished)
    
    call_count = 0
    def mock_append_rows(chunk):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("First chunk failed")

    rows = _fake_rows(25)  # enough for multiple chunks
    p_scan, p_repo, p_cache, mock_store = _patch_scan_and_repo(
        rows, repo_side_effect=mock_append_rows,
    )

    with p_scan, p_repo, p_cache:
        worker.run()
    
    qapp.processEvents()
    
    assert chunk_ready_spy.count() > 0
    assert batch_failed_spy.count() >= 1
    assert finished_spy.count() == 1
    assert worker.failed_count > 0


def test_scanner_worker_failed_count_property(temp_album, qapp):
    """Test that failed_count property exposes accumulated failures."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    assert worker.failed_count == 0
    
    rows = _fake_rows(15)
    p_scan, p_repo, p_cache, mock_store = _patch_scan_and_repo(
        rows, repo_side_effect=Exception("All writes fail"),
    )

    with p_scan, p_repo, p_cache:
        worker.run()
    
    qapp.processEvents()
    
    assert worker.failed_count > 0
    assert isinstance(worker.failed_count, int)


def test_scanner_worker_cleanup_on_error(temp_album, qapp):
    """Test that scanner is properly cleaned up even when errors occur."""
    signals = ScannerSignals()
    worker = ScannerWorker(temp_album, ["*.jpg"], [], signals)
    
    with patch('iPhoto.library.workers.scanner_worker.scan_album') as mock_scan, \
         patch('iPhoto.library.workers.scanner_worker.load_incremental_index_cache', return_value={}), \
         patch('iPhoto.library.workers.scanner_worker.get_global_repository'):
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
    p_scan, p_repo, p_cache, mock_store = _patch_scan_and_repo(rows)
    mock_store.close = Mock()

    with p_scan, p_repo, p_cache:
        worker.run()
    
    qapp.processEvents()
    
    mock_store.close.assert_not_called()
