"""Background task for scanning albums using AlbumService."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from src.iPhoto.events.bus import EventBus
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.application.use_cases.scan_album import AlbumScanProgressEvent, AlbumScannedEvent

if TYPE_CHECKING:
    from src.iPhoto.di.container import DependencyContainer

LOGGER = logging.getLogger(__name__)

class ScanRunnable(QRunnable):
    def __init__(self, service: AlbumService, album_id: str):
        super().__init__()
        self._service = service
        self._album_id = album_id

    def run(self):
        try:
            # This blocks until scan is complete
            # Progress is reported via EventBus
            self._service.scan_album(self._album_id)
        except Exception as e:
            LOGGER.error(f"Scan failed for {self._album_id}: {e}", exc_info=True)

class BackgroundScanner(QObject):
    """
    Manages background scanning tasks.
    Listens to EventBus for progress updates and emits Qt signals.
    """

    # Matching StatusBarController signature: root, current, total
    scanProgress = Signal(Path, int, int)
    # Matching StatusBarController signature: root, success
    scanFinished = Signal(Path, bool)

    # Extra signal for detailed results if needed
    scanResult = Signal(Path, int, int, int) # added, updated, deleted

    def __init__(self, container: DependencyContainer, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._container = container
        self._service = container.resolve(AlbumService)
        self._event_bus = container.resolve(EventBus)
        self._thread_pool = QThreadPool.globalInstance()

        self._active_scans: Dict[str, Path] = {}

        # Subscribe to events
        self._event_bus.subscribe(AlbumScanProgressEvent, self._on_progress)
        self._event_bus.subscribe(AlbumScannedEvent, self._on_finished)

    def scan(self, album_id: str, path: Path):
        """Start a background scan for the given album ID."""
        self._active_scans[album_id] = path

        # Signal start (optional, but StatusBarController handles implicit start via progress)
        # We could emit progress(path, 0, -1) to show indeterminate or 0/0
        self.scanProgress.emit(path, 0, 0)

        runnable = ScanRunnable(self._service, album_id)
        self._thread_pool.start(runnable)

    def _on_progress(self, event: AlbumScanProgressEvent):
        path = self._active_scans.get(event.album_id)
        if path:
            self.scanProgress.emit(path, event.processed_count, event.total_found)

    def _on_finished(self, event: AlbumScannedEvent):
        path = self._active_scans.pop(event.album_id, None)
        if path:
            self.scanResult.emit(
                path,
                event.added_count,
                event.updated_count,
                event.deleted_count
            )
            self.scanFinished.emit(path, True)
