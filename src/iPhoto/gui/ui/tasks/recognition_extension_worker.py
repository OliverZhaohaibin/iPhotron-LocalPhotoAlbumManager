"""Qt transport for verified recognition extension installation."""

from PySide6.QtCore import QObject, QRunnable, Signal

from iPhoto.application.ports.extension_install import ExtensionInstallError
from iPhoto.application.ports.recognition_extension import RecognitionExtensionRequest
from iPhoto.application.services.recognition_extension_service import RecognitionExtensionService
from iPhoto.utils.logging import get_logger

_LOG = get_logger()


class RecognitionExtensionSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)


class RecognitionExtensionWorker(QRunnable):
    def __init__(
        self, request: RecognitionExtensionRequest, service: RecognitionExtensionService
    ) -> None:
        super().__init__()
        self.signals = RecognitionExtensionSignals()
        self._request = request
        self._service = service

    def run(self) -> None:
        try:
            result = self._service.execute(self._request, self.signals.progress.emit)
        except ExtensionInstallError as exc:
            result = exc
        except Exception:
            _LOG.exception("Recognition extension installation failed unexpectedly")
            result = ExtensionInstallError("installation", "install")
        self.signals.finished.emit(result)
