"""Library update Qt adapter.

Thin Qt presentation object that owns only signal relay and worker
coordination.  All business decision-making is delegated to application-layer
services.

Design contract (Phase 3):
- This adapter MUST NOT contain business rules.
- Business decisions belong in ``application/services/`` or
  ``application/use_cases/``.
- This adapter's only responsibilities are:
  1. Forward signals between the application layer and the UI layer.
  2. Trigger use-case execution when Qt events arrive.
  3. Convert application-layer results into Qt signal payloads.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

from ....utils.logging import get_logger

if TYPE_CHECKING:
    from ....gui.services.library_update_service import LibraryUpdateService

LOGGER = get_logger()


class LibraryUpdateAdapter(QObject):
    """Qt adapter that forwards library-update events to UI subscribers.

    This adapter wraps a
    :class:`~iPhoto.gui.services.library_update_service.LibraryUpdateService`
    and re-exposes its signals under a stable presentation-layer interface.
    New UI code should connect to this adapter rather than directly to the
    underlying service, so the service can evolve without breaking UI
    consumers.
    """

    # -- Forwarded signals --------------------------------------------------
    indexUpdated = Signal(Path)
    linksUpdated = Signal(Path)
    assetReloadRequested = Signal(Path, bool, bool)
    errorRaised = Signal(str)
    scanProgress = Signal(Path, int, int)
    scanChunkReady = Signal(Path, list)
    scanFinished = Signal(Path, bool)

    def __init__(
        self,
        update_service_getter: Callable[[], LibraryUpdateService | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._update_service_getter = update_service_getter

    # ------------------------------------------------------------------
    # Service wiring
    # ------------------------------------------------------------------

    def wire_service(self, service: LibraryUpdateService) -> None:
        """Connect *service* signals to this adapter's relay slots.

        Call once after the service is created.  Subsequent calls replace the
        previously wired service.
        """

        service.indexUpdated.connect(self._on_index_updated)
        service.linksUpdated.connect(self._on_links_updated)
        service.assetReloadRequested.connect(self._on_asset_reload_requested)
        service.errorRaised.connect(self._on_error_raised)
        service.scanProgress.connect(self._on_scan_progress)
        service.scanChunkReady.connect(self._on_scan_chunk_ready)
        service.scanFinished.connect(self._on_scan_finished)
        LOGGER.debug("LibraryUpdateAdapter: wired service %r", service)

    # ------------------------------------------------------------------
    # Public coordination helpers
    # ------------------------------------------------------------------

    def announce_refresh(
        self,
        root: Path,
        *,
        request_reload: bool = True,
        announce_index: bool = False,
        force_reload: bool = False,
    ) -> None:
        """Emit index/links refresh signals and optionally request a reload."""

        service = self._update_service_getter()
        if service is not None:
            service.announce_album_refresh(
                root,
                request_reload=request_reload,
                announce_index=announce_index,
                force_reload=force_reload,
            )

    # ------------------------------------------------------------------
    # Relay slots
    # ------------------------------------------------------------------

    @Slot(Path)
    def _on_index_updated(self, root: Path) -> None:
        self.indexUpdated.emit(root)

    @Slot(Path)
    def _on_links_updated(self, root: Path) -> None:
        self.linksUpdated.emit(root)

    @Slot(Path, bool, bool)
    def _on_asset_reload_requested(self, root: Path, announce: bool, force: bool) -> None:
        self.assetReloadRequested.emit(root, announce, force)

    @Slot(str)
    def _on_error_raised(self, message: str) -> None:
        self.errorRaised.emit(message)

    @Slot(Path, int, int)
    def _on_scan_progress(self, root: Path, current: int, total: int) -> None:
        self.scanProgress.emit(root, current, total)

    @Slot(Path, list)
    def _on_scan_chunk_ready(self, root: Path, chunk: list) -> None:
        self.scanChunkReady.emit(root, chunk)

    @Slot(Path, bool)
    def _on_scan_finished(self, root: Path, success: bool) -> None:
        self.scanFinished.emit(root, success)


__all__ = ["LibraryUpdateAdapter"]
