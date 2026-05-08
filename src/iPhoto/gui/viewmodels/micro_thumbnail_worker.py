"""Background worker for decoding micro thumbnails off the GUI thread."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal

from iPhoto.utils import image_loader

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


LOGGER = logging.getLogger(__name__)


class MicroThumbnailSignals(QObject):
    """Signals emitted by MicroThumbnailWorker."""

    decoded = Signal(int, int, object)
    """Emitted when a micro thumbnail has been decoded. Args: row, generation, QImage or None"""

    failed = Signal(int, int)
    """Emitted when decoding fails for a row. Args: row, generation"""


class MicroThumbnailWorker(QRunnable):
    """Decode micro thumbnail bytes to QImage in a background thread.

    QImage can be safely created and used across threads (unlike QPixmap),
    so we decode in the worker and return the QImage to the GUI thread.
    """

    def __init__(
        self,
        row: int,
        raw_bytes: bytes,
        generation: int = 0,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._row = row
        self._raw_bytes = raw_bytes
        self._generation = generation
        self.signals = MicroThumbnailSignals()

    def run(self) -> None:
        try:
            image = image_loader.qimage_from_bytes(self._raw_bytes)
            if image is None or image.isNull():
                self.signals.failed.emit(self._row, self._generation)
            else:
                self.signals.decoded.emit(self._row, self._generation, image)
        except Exception as exc:
            LOGGER.exception("Micro thumbnail decode failed for row %d", self._row)
            self.signals.failed.emit(self._row, self._generation)


__all__ = ["MicroThumbnailSignals", "MicroThumbnailWorker"]
