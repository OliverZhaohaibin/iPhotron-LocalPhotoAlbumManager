"""Pure Python AlbumTreeViewModel — no Qt dependency.

Manages album tree state: loading, selection, and scan lifecycle.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import AlbumOpenedEvent, ScanCompletedEvent
from iPhoto.gui.viewmodels.base import BaseViewModel
from iPhoto.gui.viewmodels.signal import ObservableProperty, Signal


class AlbumTreeViewModel(BaseViewModel):
    """Album tree ViewModel — pure Python."""

    def __init__(
        self,
        album_service: Any,
        event_bus: EventBus,
    ) -> None:
        super().__init__()
        self._album_service = album_service
        self._event_bus = event_bus
        self._logger = logging.getLogger(__name__)

        # Observable properties
        self.current_album_id = ObservableProperty(None)
        self.current_album_path = ObservableProperty(None)
        self.albums = ObservableProperty([])
        self.loading = ObservableProperty(False)
        self.scan_progress = ObservableProperty(0.0)

        # Signals for one-shot notifications
        self.album_opened = Signal()
        self.scan_finished = Signal()
        self.error_occurred = Signal()

        # Event subscriptions
        self.subscribe_event(event_bus, ScanCompletedEvent, self._on_scan_completed)

    def open_album(self, path: Path) -> None:
        """Open an album by path."""
        self.loading.value = True
        try:
            response = self._album_service.open_album(path)
            album_id = getattr(response, "album_id", None) or getattr(response, "id", None)
            self.current_album_id.value = album_id
            self.current_album_path.value = str(path)
            self.album_opened.emit(response)

            self._event_bus.publish(AlbumOpenedEvent(album_id=album_id or "", album_path=str(path)))
        except Exception as exc:
            self._logger.error("Failed to open album: %s", exc)
            self.error_occurred.emit(str(exc))
        finally:
            self.loading.value = False

    def scan_current_album(self) -> None:
        """Trigger a scan of the currently selected album."""
        album_id = self.current_album_id.value
        if not album_id:
            return
        self.loading.value = True
        self.scan_progress.value = 0.0
        try:
            self._album_service.scan_album(album_id)
            self.scan_finished.emit()
        except Exception as exc:
            self._logger.error("Scan failed: %s", exc)
            self.error_occurred.emit(str(exc))
        finally:
            self.loading.value = False

    def select_album(self, album_id: Optional[str]) -> None:
        """Change the currently selected album."""
        self.current_album_id.value = album_id

    def _on_scan_completed(self, event: ScanCompletedEvent) -> None:
        if event.album_id == self.current_album_id.value:
            self.scan_progress.value = 1.0
            self.loading.value = False
            self.scan_finished.emit()
