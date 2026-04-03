"""Background worker that extracts a representative video frame for sidebar previews."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage

from ....core.color_resolver import ColorStats, compute_color_statistics
from .video_frame_grabber import grab_video_frame

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoSidebarPreviewResult:
    """Container bundling the decoded preview frame and sampled colour stats."""

    image: QImage
    stats: ColorStats | None


class VideoSidebarPreviewSignals(QObject):
    """Signals emitted by :class:`VideoSidebarPreviewWorker`."""

    ready = Signal(VideoSidebarPreviewResult, int)
    error = Signal(int, str)
    finished = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class VideoSidebarPreviewWorker(QRunnable):
    """Decode a representative frame for the edit sidebar off the GUI thread."""

    def __init__(
        self,
        source: Path,
        *,
        generation: int,
        target_size: QSize,
        still_image_time: float | None,
        duration: float | None,
        trim_in_sec: float | None,
        trim_out_sec: float | None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._source = source
        self._generation = int(generation)
        self._target_size = QSize(max(target_size.width(), 1), max(target_size.height(), 1))
        self._still_image_time = still_image_time
        self._duration = duration
        self._trim_in_sec = trim_in_sec
        self._trim_out_sec = trim_out_sec
        self.signals = VideoSidebarPreviewSignals()

    def run(self) -> None:  # pragma: no cover - worker thread
        try:
            image = grab_video_frame(
                self._source,
                self._target_size,
                still_image_time=self._still_image_time,
                duration=self._duration,
                trim_in_sec=self._trim_in_sec,
                trim_out_sec=self._trim_out_sec,
            )
            if image is None or image.isNull():
                self.signals.error.emit(self._generation, "Sidebar preview frame was empty")
                return
            stats: ColorStats | None
            try:
                stats = compute_color_statistics(image)
            except Exception:
                _LOGGER.exception("Failed to compute video sidebar color statistics")
                stats = None
            self.signals.ready.emit(
                VideoSidebarPreviewResult(QImage(image), stats),
                self._generation,
            )
        except Exception as exc:
            _LOGGER.exception("Failed to prepare video sidebar preview")
            self.signals.error.emit(self._generation, str(exc))
        finally:
            self.signals.finished.emit(self._generation)


__all__ = [
    "VideoSidebarPreviewResult",
    "VideoSidebarPreviewSignals",
    "VideoSidebarPreviewWorker",
]
