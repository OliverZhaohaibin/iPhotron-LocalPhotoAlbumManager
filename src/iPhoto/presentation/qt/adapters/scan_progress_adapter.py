"""Scan progress Qt adapter.

Thin Qt presentation object whose sole responsibility is relaying scan
progress signals from a background worker or service to UI subscribers.

Design contract (Phase 3):
- No business rules.
- No scan scheduling logic.
- Only signal relay and optional throttling/batching of high-frequency events.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ....utils.logging import get_logger

LOGGER = get_logger()


class ScanProgressAdapter(QObject):
    """Qt adapter that relays scan progress events to UI subscribers.

    New presentation code should connect to this adapter rather than directly
    to the scanner worker or
    :class:`~iPhoto.gui.services.library_update_service.LibraryUpdateService`,
    so the scanning infrastructure can evolve independently.
    """

    # -- Forwarded signals --------------------------------------------------
    scanProgress = Signal(Path, int, int)
    """Emitted periodically while a scan is in progress.

    Arguments: ``(album_root, scanned_count, total_count)``.
    """

    scanChunkReady = Signal(Path, list)
    """Emitted when a batch of index rows is ready for incremental display.

    Arguments: ``(album_root, rows)``.
    """

    scanFinished = Signal(Path, bool)
    """Emitted once when the scan completes.

    Arguments: ``(album_root, success)``.
    """

    scanBatchFailed = Signal(Path, int)
    """Emitted when a partial batch of files could not be processed.

    Arguments: ``(album_root, failed_count)``.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    # ------------------------------------------------------------------
    # Relay slots - connect scanner workers to these
    # ------------------------------------------------------------------

    @Slot(Path, int, int)
    def relay_progress(self, root: Path, current: int, total: int) -> None:
        """Forward a progress update from a scanner worker."""

        self.scanProgress.emit(root, current, total)

    @Slot(Path, list)
    def relay_chunk_ready(self, root: Path, chunk: list) -> None:
        """Forward a chunk-ready notification from a scanner worker."""

        self.scanChunkReady.emit(root, chunk)

    @Slot(Path, bool)
    def relay_finished(self, root: Path, success: bool) -> None:
        """Forward scan completion from a scanner worker."""

        self.scanFinished.emit(root, success)

    @Slot(Path, int)
    def relay_batch_failed(self, root: Path, count: int) -> None:
        """Forward a partial-failure notification from a scanner worker."""

        self.scanBatchFailed.emit(root, count)


__all__ = ["ScanProgressAdapter"]
