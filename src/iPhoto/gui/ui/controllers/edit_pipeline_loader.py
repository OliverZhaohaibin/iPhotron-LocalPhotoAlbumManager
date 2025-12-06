"""Manages asynchronous resource loading for the edit workflow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QThreadPool, Signal, QSize
from PySide6.QtGui import QImage

from ..tasks.image_load_worker import ImageLoadWorker
from ..tasks.edit_sidebar_preview_worker import (
    EditSidebarPreviewResult,
    EditSidebarPreviewWorker,
)

if TYPE_CHECKING:
    from .edit_preview_manager import EditPreviewManager

_LOGGER = logging.getLogger(__name__)


class EditPipelineLoader(QObject):
    """Orchestrates background image loading and preview generation."""

    imageLoaded = Signal(Path, QImage)
    """Emitted when the full-resolution image is ready for editing."""

    imageLoadFailed = Signal(Path, str)
    """Emitted when the source image cannot be loaded."""

    sidebarPreviewReady = Signal(EditSidebarPreviewResult)
    """Emitted when a sidebar preview thumbnail is available."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._active_image_worker: ImageLoadWorker | None = None
        self._sidebar_preview_worker: EditSidebarPreviewWorker | None = None

        # Generation tracking to prevent stale updates
        self._sidebar_preview_generation = 0
        self._sidebar_preview_worker_generation: int | None = None

    def load_image(self, source: Path) -> None:
        """Start loading the full resolution image in the background."""
        # Reset any previous worker reference
        self._active_image_worker = None

        worker = ImageLoadWorker(source)
        worker.signals.imageLoaded.connect(self._on_image_loaded)
        worker.signals.loadFailed.connect(self._on_image_load_failed)
        self._active_image_worker = worker
        QThreadPool.globalInstance().start(worker)

    def prepare_sidebar_preview(
        self,
        image: QImage,
        target_height: int,
        *,
        full_res_image_for_fallback: QImage | None = None,
    ) -> None:
        """Queue generation of a sidebar preview thumbnail.

        Parameters
        ----------
        image:
            The source image (usually a GPU-scaled preview).
        target_height:
            The desired height for the preview.
        full_res_image_for_fallback:
            Optional original full-res image if the primary image is unscaled.
        """
        if image.isNull():
            return

        self._sidebar_preview_generation += 1
        generation = self._sidebar_preview_generation

        # Determine whether the worker should perform an additional scaling pass.
        if image.height() > int(target_height * 1.5):
            worker_image = full_res_image_for_fallback if full_res_image_for_fallback else image
            worker_target_height = target_height
        else:
            worker_image = image
            worker_target_height = -1

        worker = EditSidebarPreviewWorker(
            worker_image,
            generation=generation,
            target_height=worker_target_height,
        )
        worker.signals.ready.connect(self._handle_sidebar_preview_ready)
        worker.signals.error.connect(self._handle_sidebar_preview_error)
        worker.signals.finished.connect(self._handle_sidebar_preview_finished)
        self._sidebar_preview_worker = worker
        self._sidebar_preview_worker_generation = generation
        QThreadPool.globalInstance().start(worker)

    def cancel_pending_operations(self) -> None:
        """Cancel any pending load or preview operations (where possible)."""
        # Note: QRunnable cannot be easily cancelled once started,
        # but we can detach listeners or ignore results via generation checks.
        self._active_image_worker = None
        # Increment generation to invalidate in-flight preview workers
        self._sidebar_preview_generation += 1

    def _on_image_loaded(self, path: Path, image: QImage) -> None:
        # Don't check for self._active_image_worker is None here.
        # This fixes a race condition where a new load request replaces _active_image_worker,
        # but the previous worker finishes and clears it, leaving the new one active but the variable None.

        # We also don't strictly clear _active_image_worker here because a newer one might have been started.
        # If we really want to track which one is active, we need IDs.
        # But for now, just emitting is safer, and let EditController filter by path.
        self.imageLoaded.emit(path, image)

    def _on_image_load_failed(self, path: Path, message: str) -> None:
        # Same logic as above.
        self.imageLoadFailed.emit(path, message)

    def _handle_sidebar_preview_ready(
        self,
        result: EditSidebarPreviewResult,
        generation: int,
    ) -> None:
        if generation != self._sidebar_preview_generation:
            return
        self.sidebarPreviewReady.emit(result)

    def _handle_sidebar_preview_error(self, generation: int, message: str) -> None:
        if generation != self._sidebar_preview_generation:
            return
        _LOGGER.error("Edit sidebar preview preparation failed: %s", message)

    def _handle_sidebar_preview_finished(self, generation: int) -> None:
        if generation == self._sidebar_preview_worker_generation:
            self._sidebar_preview_worker = None
            self._sidebar_preview_worker_generation = None
