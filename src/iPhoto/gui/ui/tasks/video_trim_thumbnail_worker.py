"""Background worker that generates timeline thumbnails for the trim bar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage

from .video_frame_grabber import grab_video_frame


class VideoTrimThumbnailSignals(QObject):
    """Signals emitted by :class:`VideoTrimThumbnailWorker`."""

    ready = Signal(list)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class VideoTrimThumbnailWorker(QRunnable):
    """Generate representative thumbnails across a video's duration."""

    def __init__(
        self,
        source: Path,
        *,
        duration_sec: float | None,
        target_height: int,
        target_width: int,
        count: int = 10,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._source = source
        self._duration_sec = duration_sec
        self._target_height = max(int(target_height), 48)
        self._target_width = max(int(target_width), 64)
        self._count = max(int(count), 1)
        self.signals = VideoTrimThumbnailSignals()

    def run(self) -> None:  # pragma: no cover - worker thread
        try:
            images: list[QImage] = []
            sample_times = self._sample_times()
            if not sample_times:
                image = grab_video_frame(
                    self._source,
                    QSize(self._target_width, self._target_height),
                    still_image_time=None,
                    duration=self._duration_sec,
                )
                if image is not None and not image.isNull():
                    images.append(QImage(image))
            else:
                for sample_time in sample_times:
                    image = grab_video_frame(
                        self._source,
                        QSize(self._target_width, self._target_height),
                        still_image_time=sample_time,
                        duration=self._duration_sec,
                    )
                    if image is None or image.isNull():
                        continue
                    images.append(QImage(image))
            self.signals.ready.emit(images)
        except Exception as exc:
            self.signals.error.emit(str(exc))

    def _sample_times(self) -> list[float]:
        duration = self._duration_sec
        if duration is None or duration <= 0.0:
            return []
        if self._count == 1:
            return [duration * 0.5]
        segment = duration / float(self._count)
        return [
            min(duration, (index + 0.5) * segment)
            for index in range(self._count)
        ]


__all__ = ["VideoTrimThumbnailWorker", "VideoTrimThumbnailSignals"]
