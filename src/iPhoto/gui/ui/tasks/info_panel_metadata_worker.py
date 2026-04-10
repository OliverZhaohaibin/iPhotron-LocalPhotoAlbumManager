"""Background worker that enriches sparse metadata for the detail info panel."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from iPhoto.errors import ExternalToolError
from iPhoto.io.metadata import read_image_meta, read_video_meta
from iPhoto.utils.exiftool import get_metadata_batch

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class InfoPanelMetadataResult:
    """Fresh metadata extracted for a single asset."""

    path: Path
    metadata: dict[str, Any]


class InfoPanelMetadataSignals(QObject):
    """Signals emitted by :class:`InfoPanelMetadataWorker`."""

    ready = Signal(InfoPanelMetadataResult)
    error = Signal(str, str)
    finished = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class InfoPanelMetadataWorker(QRunnable):
    """Extract missing info-panel metadata off the GUI thread."""

    def __init__(self, path: Path, *, is_video: bool) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = Path(path)
        self._is_video = bool(is_video)
        self.signals = InfoPanelMetadataSignals()

    def run(self) -> None:  # pragma: no cover - exercised through coordinator tests
        path_key = str(self._path)
        try:
            metadata = self._read_metadata()
            self.signals.ready.emit(
                InfoPanelMetadataResult(path=self._path, metadata=metadata),
            )
        except Exception as exc:  # noqa: BLE001 - keep worker failures isolated
            _LOGGER.debug(
                "Failed to enrich info-panel metadata for %s",
                self._path,
                exc_info=True,
            )
            self.signals.error.emit(path_key, str(exc))
        finally:
            self.signals.finished.emit(path_key)

    def _read_metadata(self) -> dict[str, Any]:
        if self._is_video:
            exif_payload = None
            try:
                exif_batch = get_metadata_batch([self._path])
                exif_payload = exif_batch[0] if exif_batch else None
            except (ExternalToolError, OSError):
                _LOGGER.debug(
                    "ExifTool metadata fetch failed for %s",
                    self._path,
                    exc_info=True,
                )
            return read_video_meta(self._path, exif_payload)
        return read_image_meta(self._path)


__all__ = [
    "InfoPanelMetadataResult",
    "InfoPanelMetadataSignals",
    "InfoPanelMetadataWorker",
]
