"""Pure Python AssetListViewModel (MVVM) — no Qt dependency.

Manages the asset list state: loading, selection, pagination.
The existing Qt-based ``AssetListViewModel`` (QAbstractListModel) is preserved
as ``asset_list_viewmodel.py`` for backward compatibility; this pure-Python
version is used by the MVVM layer and bridged to Qt via adapters.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanCompletedEvent, AssetImportedEvent
from iPhoto.gui.viewmodels.base import BaseViewModel
from iPhoto.gui.viewmodels.signal import ObservableProperty, Signal


class PureAssetListViewModel(BaseViewModel):
    """Asset list ViewModel — pure Python, no Qt dependency."""

    def __init__(
        self,
        data_source: Any,
        thumbnail_cache: Any,
        event_bus: EventBus,
    ) -> None:
        super().__init__()
        self._data_source = data_source
        self._thumbnail_cache = thumbnail_cache
        self._logger = logging.getLogger(__name__)

        # Observable properties
        self.assets = ObservableProperty([])
        self.selected_indices = ObservableProperty([])
        self.loading = ObservableProperty(False)
        self.total_count = ObservableProperty(0)
        self.current_album_id = ObservableProperty(None)

        # Signals
        self.selection_changed = Signal()
        self.assets_updated = Signal()
        self.error_occurred = Signal()

        # Event subscriptions
        self.subscribe_event(event_bus, ScanCompletedEvent, self._on_scan_completed)
        self.subscribe_event(event_bus, AssetImportedEvent, self._on_assets_imported)

    def load_album(self, album_id: str) -> None:
        """Load assets for a given album."""
        self.loading.value = True
        self.current_album_id.value = album_id
        try:
            assets = self._data_source.load_assets(album_id)
            self.assets.value = assets
            self.total_count.value = len(assets)
            self.selected_indices.value = []
            self.assets_updated.emit(assets)
        except Exception as exc:
            self._logger.error("Failed to load album assets: %s", exc)
            self.error_occurred.emit(str(exc))
        finally:
            self.loading.value = False

    def select(self, index: int) -> None:
        """Add *index* to the selection."""
        current = list(self.selected_indices.value)
        if index not in current:
            current.append(index)
            self.selected_indices.value = current
            self.selection_changed.emit(current)

    def deselect(self, index: int) -> None:
        """Remove *index* from the selection."""
        current = list(self.selected_indices.value)
        if index in current:
            current.remove(index)
            self.selected_indices.value = current
            self.selection_changed.emit(current)

    def clear_selection(self) -> None:
        """Clear all selections."""
        if self.selected_indices.value:
            self.selected_indices.value = []
            self.selection_changed.emit([])

    def get_thumbnail(self, asset_id: str) -> Optional[bytes]:
        """Retrieve a cached thumbnail for *asset_id*."""
        return self._thumbnail_cache.get(asset_id)

    def get_asset(self, index: int) -> Optional[Any]:
        """Return the asset at *index*, or ``None``."""
        assets = self.assets.value
        if 0 <= index < len(assets):
            return assets[index]
        return None

    # -- EventBus handlers --------------------------------------------------

    def _on_scan_completed(self, event: ScanCompletedEvent) -> None:
        if self.current_album_id.value and event.album_id == self.current_album_id.value:
            self.load_album(event.album_id)

    def _on_assets_imported(self, event: AssetImportedEvent) -> None:
        if self.current_album_id.value and event.album_id == self.current_album_id.value:
            self.load_album(event.album_id)
