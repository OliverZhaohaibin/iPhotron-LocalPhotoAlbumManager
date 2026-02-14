"""Pure Python DetailViewModel — no Qt dependency.

Manages detail view state: current asset, metadata, editing state.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from iPhoto.events.bus import EventBus
from iPhoto.gui.viewmodels.base import BaseViewModel
from iPhoto.gui.viewmodels.signal import ObservableProperty, Signal


class DetailViewModel(BaseViewModel):
    """Detail / single-asset ViewModel — pure Python."""

    def __init__(
        self,
        asset_service: Any,
        event_bus: EventBus,
    ) -> None:
        super().__init__()
        self._asset_service = asset_service
        self._event_bus = event_bus
        self._logger = logging.getLogger(__name__)

        # Observable properties
        self.current_asset = ObservableProperty(None)
        self.metadata = ObservableProperty({})
        self.is_favorite = ObservableProperty(False)
        self.loading = ObservableProperty(False)
        self.editing = ObservableProperty(False)

        # Signals
        self.asset_changed = Signal()
        self.favorite_toggled = Signal()
        self.error_occurred = Signal()

    def load_asset(self, asset_id: str) -> None:
        """Load a single asset by ID."""
        self.loading.value = True
        try:
            asset = self._asset_service.get_asset(asset_id)
            if asset is None:
                self.error_occurred.emit(f"Asset not found: {asset_id}")
                return
            self.current_asset.value = asset
            self.is_favorite.value = getattr(asset, "is_favorite", False)
            meta = getattr(asset, "metadata", None)
            self.metadata.value = meta if isinstance(meta, dict) else {}
            self.asset_changed.emit(asset)
        except Exception as exc:
            self._logger.error("Failed to load asset: %s", exc)
            self.error_occurred.emit(str(exc))
        finally:
            self.loading.value = False

    def toggle_favorite(self) -> None:
        """Toggle favourite status of the current asset."""
        asset = self.current_asset.value
        if asset is None:
            return
        asset_id = getattr(asset, "id", None)
        if not asset_id:
            return
        try:
            new_status = self._asset_service.toggle_favorite(asset_id)
            self.is_favorite.value = new_status
            self.favorite_toggled.emit(new_status)
        except Exception as exc:
            self._logger.error("Failed to toggle favorite: %s", exc)
            self.error_occurred.emit(str(exc))

    def update_metadata(self, updates: Dict[str, Any]) -> None:
        """Update metadata for the current asset."""
        asset = self.current_asset.value
        if asset is None:
            return
        asset_id = getattr(asset, "id", None)
        if not asset_id:
            return
        try:
            self._asset_service.update_metadata(asset_id, updates)
            current_meta = dict(self.metadata.value)
            current_meta.update(updates)
            self.metadata.value = current_meta
        except Exception as exc:
            self._logger.error("Failed to update metadata: %s", exc)
            self.error_occurred.emit(str(exc))

    def set_editing(self, editing: bool) -> None:
        """Enter or leave editing mode."""
        self.editing.value = editing

    def clear(self) -> None:
        """Reset state when navigating away."""
        self.current_asset.value = None
        self.metadata.value = {}
        self.is_favorite.value = False
        self.editing.value = False
