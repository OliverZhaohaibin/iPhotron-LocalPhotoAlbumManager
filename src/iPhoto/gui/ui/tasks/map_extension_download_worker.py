"""Qt transport for the process-scoped map extension application service."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from iPhoto.application.ports.map_extension import (
    MapExtensionError,
)
from iPhoto.application.ports.map_extension import (
    MapExtensionRequest as MapExtensionDownloadRequest,
)
from iPhoto.application.ports.map_extension import (
    MapExtensionResult as MapExtensionDownloadResult,
)
from iPhoto.application.services.map_extension_service import MapExtensionService


class MapExtensionDownloadSignals(QObject):
    progress = Signal(int, int, str)
    ready = Signal(object)
    error = Signal(object)
    finished = Signal()


class MapExtensionDownloadWorker(QRunnable):
    def __init__(self, request_payload: MapExtensionDownloadRequest, service: MapExtensionService):
        super().__init__()
        self.setAutoDelete(True)
        self._request = request_payload
        self._service = service
        self.signals = MapExtensionDownloadSignals()

    def run(self):
        try:
            result = self._service.execute(self._request, self.signals.progress.emit)
            self.signals.ready.emit(result)
        except MapExtensionError as exc:
            self.signals.error.emit(exc)
        except Exception:
            # Never expose unknown exception strings (URLs may carry credentials).
            self.signals.error.emit(MapExtensionError("installation", self._request.operation))
        finally:
            self.signals.finished.emit()


__all__ = [
    "MapExtensionDownloadRequest",
    "MapExtensionDownloadResult",
    "MapExtensionDownloadSignals",
    "MapExtensionDownloadWorker",
]
