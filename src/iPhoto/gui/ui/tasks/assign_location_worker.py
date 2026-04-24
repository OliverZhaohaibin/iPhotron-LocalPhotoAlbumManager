"""Background worker that embeds and persists an assigned location."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from iPhoto.application.services.assign_location_service import (
    AssignLocationService,
    AssignedLocationResult,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssignLocationRequest:
    library_root: Path
    asset_path: Path
    asset_rel: str
    display_name: str
    latitude: float
    longitude: float
    is_video: bool
    existing_metadata: dict[str, Any] | None = None


class AssignLocationSignals(QObject):
    ready = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class AssignLocationWorker(QRunnable):
    """Run metadata embedding and DB persistence off the GUI thread."""

    def __init__(self, request: AssignLocationRequest) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._request = request
        self.signals = AssignLocationSignals()

    def run(self) -> None:  # pragma: no cover - exercised through GUI integration
        try:
            service = AssignLocationService(self._request.library_root)
            result = service.assign(
                asset_path=self._request.asset_path,
                asset_rel=self._request.asset_rel,
                display_name=self._request.display_name,
                latitude=self._request.latitude,
                longitude=self._request.longitude,
                is_video=self._request.is_video,
                existing_metadata=self._request.existing_metadata,
            )
            self.signals.ready.emit(result)
        except Exception as exc:  # noqa: BLE001 - keep worker failures isolated
            _LOGGER.exception("Failed to assign location for %s", self._request.asset_path)
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


__all__ = [
    "AssignLocationRequest",
    "AssignLocationSignals",
    "AssignLocationWorker",
    "AssignedLocationResult",
]
