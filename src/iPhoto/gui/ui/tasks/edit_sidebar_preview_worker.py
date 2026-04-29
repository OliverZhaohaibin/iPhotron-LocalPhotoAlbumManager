"""Background worker that prepares preview assets for the edit sidebar."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage

from ....core.color_resolver import ColorStats, compute_color_statistics
from .image_scaling import scale_qimage_to_height_for_worker

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EditSidebarPreviewResult:
    """Container bundling the scaled preview image and sampled colour stats."""

    image: QImage
    stats: ColorStats


class EditSidebarPreviewSignals(QObject):
    """Signals emitted by :class:`EditSidebarPreviewWorker`."""

    ready = Signal(EditSidebarPreviewResult, int)
    """Delivered when the preview image and colour statistics are available."""

    error = Signal(int, str)
    """Emitted if any unexpected exception aborts the worker."""

    finished = Signal(int)
    """Emitted once the worker has completed, even on failure."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


def build_edit_sidebar_preview(source_image: QImage, target_height: int) -> EditSidebarPreviewResult:
    """Build a sidebar preview synchronously from *source_image*."""

    if source_image.isNull():
        raise ValueError("Sidebar preview source image was empty")

    requested_height = int(target_height)
    normalized_target_height = -1 if requested_height < 0 else max(64, requested_height)
    if normalized_target_height == -1:
        preview = QImage(source_image)
        if preview.format() != QImage.Format.Format_ARGB32:
            preview = preview.convertToFormat(QImage.Format.Format_ARGB32)
    else:
        preview = scale_qimage_to_height_for_worker(source_image, normalized_target_height)

    stats = compute_color_statistics(preview)
    return EditSidebarPreviewResult(preview, stats)


class EditSidebarPreviewWorker(QRunnable):
    """Scale full-resolution frames for the edit sidebar off the GUI thread."""

    def __init__(
        self,
        source_image: QImage,
        *,
        generation: int,
        target_height: int,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)

        # ``QImage`` implements implicit data sharing, however copying it here keeps the caller's
        # instance detached.  The worker may mutate pixels while filtering, so a deep copy avoids
        # cross-thread races and prevents Qt from throwing warnings about detached buffers.
        self._source_image = QImage(source_image)
        self._generation = int(generation)
        requested_height = int(target_height)
        self._target_height = -1 if requested_height < 0 else max(64, requested_height)
        self.signals = EditSidebarPreviewSignals()

    # ------------------------------------------------------------------
    def run(self) -> None:  # type: ignore[override]
        """Prepare the sidebar preview image and compute colour statistics."""

        if self._source_image.isNull():
            self.signals.error.emit(self._generation, "Sidebar preview source image was empty")
            self.signals.finished.emit(self._generation)
            return

        try:
            result = build_edit_sidebar_preview(self._source_image, self._target_height)
            self.signals.ready.emit(result, self._generation)
        except Exception as exc:  # pragma: no cover - defensive logging path
            _LOGGER.exception("Failed to prepare edit sidebar preview")
            self.signals.error.emit(self._generation, str(exc))
        finally:
            self.signals.finished.emit(self._generation)


__all__ = [
    "build_edit_sidebar_preview",
    "EditSidebarPreviewResult",
    "EditSidebarPreviewSignals",
    "EditSidebarPreviewWorker",
]
