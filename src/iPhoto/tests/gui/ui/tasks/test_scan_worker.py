import pytest
from unittest.mock import Mock, call
from pathlib import Path
from src.iPhoto.gui.ui.tasks.scan_worker import ScanWorker, ScanSignals
from src.iPhoto.application.services.album_service import AlbumService

def test_scan_worker_runs_service():
    # Arrange
    service = Mock(spec=AlbumService)
    signals = Mock(spec=ScanSignals)

    path = Path("/tmp/album")
    worker = ScanWorker(service, "alb1", path, signals)

    # Act
    worker.run()

    # Assert
    # 1. Started signal
    signals.started.emit.assert_called_with(path)
    # 2. Service call
    service.scan_album.assert_called_with("alb1", force_rescan=True)
    # 3. Finished signal
    signals.finished.emit.assert_called_with(path, True)

def test_scan_worker_handles_error():
    # Arrange
    service = Mock(spec=AlbumService)
    service.scan_album.side_effect = Exception("Scan Failed")
    signals = Mock(spec=ScanSignals)

    path = Path("/tmp/album")
    worker = ScanWorker(service, "alb1", path, signals)

    # Act
    worker.run()

    # Assert
    signals.started.emit.assert_called_with(path)
    signals.error.emit.assert_called_with("Scan Failed")
    signals.finished.emit.assert_called_with(path, False)
