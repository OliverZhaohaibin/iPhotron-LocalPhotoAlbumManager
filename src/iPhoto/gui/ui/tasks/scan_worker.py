from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, Signal
from src.iPhoto.application.services.album_service import AlbumService

class ScanSignals(QObject):
    started = Signal(Path)
    finished = Signal(Path, bool)
    error = Signal(str)

class ScanWorker(QRunnable):
    def __init__(self, album_service: AlbumService, album_id: str, path: Path, signals: ScanSignals):
        super().__init__()
        self.setAutoDelete(False)
        self._service = album_service
        self._album_id = album_id
        self._path = path
        self._signals = signals

    def run(self):
        self._signals.started.emit(self._path)
        try:
            self._service.scan_album(self._album_id, force_rescan=True)
            self._signals.finished.emit(self._path, True)
        except Exception as e:
            self._signals.error.emit(str(e))
            self._signals.finished.emit(self._path, False)
