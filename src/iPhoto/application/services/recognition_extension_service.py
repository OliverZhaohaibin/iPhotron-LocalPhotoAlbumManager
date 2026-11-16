"""Install the shared People & Pets model package."""

from ..ports.extension_install import ExtensionProgress
from ..ports.recognition_extension import (
    RecognitionExtensionPort,
    RecognitionExtensionRequest,
    RecognitionExtensionResult,
)


class RecognitionExtensionService:
    def __init__(self, adapter: RecognitionExtensionPort):
        self._adapter = adapter

    @property
    def download_url(self) -> str:
        return self._adapter.download_url

    def execute(
        self, request: RecognitionExtensionRequest, progress: ExtensionProgress
    ) -> RecognitionExtensionResult:
        return self._adapter.execute(request, progress)
