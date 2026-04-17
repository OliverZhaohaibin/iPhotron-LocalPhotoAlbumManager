"""Background worker that validates and saves manual face annotations."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from iPhoto.people.pipeline import ManualFaceValidationError
from iPhoto.people.service import ManualFaceAddResult, PeopleService

_LOGGER = logging.getLogger(__name__)


class ManualFaceAddSignals(QObject):
    """Signals emitted by :class:`ManualFaceAddWorker`."""

    ready = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class ManualFaceAddWorker(QRunnable):
    """Perform manual face validation and persistence off the GUI thread."""

    def __init__(
        self,
        *,
        library_root: Path,
        asset_id: str,
        requested_box: tuple[int, int, int, int],
        name_or_none: str | None,
        person_id: str | None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._library_root = Path(library_root)
        self._asset_id = str(asset_id)
        self._requested_box = tuple(int(value) for value in requested_box)
        self._name_or_none = name_or_none
        self._person_id = person_id
        self.signals = ManualFaceAddSignals()

    def run(self) -> None:  # pragma: no cover - exercised through coordinator/widget tests
        try:
            service = PeopleService(self._library_root)
            result = service.add_manual_face(
                asset_id=self._asset_id,
                requested_box=self._requested_box,
                name_or_none=self._name_or_none,
                person_id=self._person_id,
            )
            self.signals.ready.emit(result)
        except ManualFaceValidationError as exc:
            _LOGGER.warning(
                "Manual face save rejected for asset %s with requested_box=%s person_id=%s name=%r: %s",
                self._asset_id,
                self._requested_box,
                self._person_id,
                self._name_or_none,
                exc,
            )
            self.signals.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Failed to save manual face for asset %s", self._asset_id)
            self.signals.error.emit(str(exc) or "Saving the face failed.")
        finally:
            self.signals.finished.emit()


__all__ = [
    "ManualFaceAddResult",
    "ManualFaceAddSignals",
    "ManualFaceAddWorker",
]
