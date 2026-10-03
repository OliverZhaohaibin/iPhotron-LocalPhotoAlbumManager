"""Application entry point shared by GUI installation and headless callers."""

from ..ports.map_extension import (
    MapExtensionPort,
    MapExtensionProgress,
    MapExtensionRequest,
    MapExtensionResult,
)


class MapExtensionService:
    def __init__(self, adapter: MapExtensionPort):
        self._adapter = adapter

    def execute(
        self, request: MapExtensionRequest, progress: MapExtensionProgress
    ) -> MapExtensionResult:
        if request.network_mode not in {"system", "direct"}:
            raise ValueError("Unsupported map download connection mode")
        if request.operation not in {"install", "prepare"}:
            raise ValueError("Unsupported map extension operation")
        return self._adapter.execute(request, progress)

    def download_url(self, platform: str) -> str | None:
        return self._adapter.download_url(platform)
