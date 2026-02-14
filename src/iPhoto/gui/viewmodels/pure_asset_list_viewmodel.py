"""Pure Python AssetListViewModel (MVVM) — no Qt dependency.

Manages the asset list state: loading, selection, pagination.
The existing Qt-based ``AssetListViewModel`` (QAbstractListModel) is preserved
as ``asset_list_viewmodel.py`` for backward compatibility; this pure-Python
version is used by the MVVM layer and bridged to Qt via adapters.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from iPhoto.application.services.paginated_loader import (
    PaginatedAssetLoader,
)
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanCompletedEvent, AssetImportedEvent
from iPhoto.gui.viewmodels.base import BaseViewModel
from iPhoto.gui.viewmodels.signal import ObservableProperty, Signal


class PureAssetListViewModel(BaseViewModel):
    """Asset list ViewModel — pure Python, no Qt dependency.

    When a ``paginated_loader`` is supplied the ViewModel loads assets
    page-by-page (default 200 items/page) to reduce peak memory usage.
    The legacy ``data_source.load_assets()`` path is preserved when no
    loader is provided (backward compatible).
    """

    def __init__(
        self,
        data_source: Any,
        thumbnail_cache: Any,
        event_bus: EventBus,
        paginated_loader: Optional[PaginatedAssetLoader] = None,
    ) -> None:
        super().__init__()
        self._data_source = data_source
        self._thumbnail_cache = thumbnail_cache
        self._paginated_loader = paginated_loader
        self._logger = logging.getLogger(__name__)

        # Observable properties
        self.assets = ObservableProperty([])
        self.selected_indices = ObservableProperty([])
        self.loading = ObservableProperty(False)
        self.total_count = ObservableProperty(0)
        self.current_album_id = ObservableProperty(None)

        # Pagination observable state
        self.current_page = ObservableProperty(0)
        self.has_more_pages = ObservableProperty(False)
        self.total_pages = ObservableProperty(0)

        # Signals
        self.selection_changed = Signal()
        self.assets_updated = Signal()
        self.error_occurred = Signal()
        self.page_loaded = Signal()  # emits (page_number, page_items)

        # Event subscriptions
        self.subscribe_event(event_bus, ScanCompletedEvent, self._on_scan_completed)
        self.subscribe_event(event_bus, AssetImportedEvent, self._on_assets_imported)

    def load_album(self, album_id: str) -> None:
        """Load assets for a given album.

        If a ``paginated_loader`` was provided, the first page is loaded via
        ``PaginatedAssetLoader.reset()``; further pages are fetched with
        :meth:`load_next_page`.  Otherwise, all assets are fetched in a
        single call through the legacy ``data_source.load_assets()`` path.
        """
        self.loading.value = True
        self.current_album_id.value = album_id
        try:
            if self._paginated_loader is not None:
                from iPhoto.domain.models.query import AssetQuery

                query = AssetQuery(album_id=album_id)
                result = self._paginated_loader.reset(query)
                self.assets.value = list(self._paginated_loader.items)
                self.total_count.value = result.total_count
                self.current_page.value = result.page
                self.has_more_pages.value = result.has_more
                self.total_pages.value = result.total_pages
            else:
                assets = self._data_source.load_assets(album_id)
                self.assets.value = assets
                self.total_count.value = len(assets)
                self.current_page.value = 0
                self.has_more_pages.value = False
                self.total_pages.value = 0
            self.selected_indices.value = []
            self.assets_updated.emit(self.assets.value)
        except Exception as exc:
            self._logger.error("Failed to load album assets: %s", exc)
            self.error_occurred.emit(str(exc))
        finally:
            self.loading.value = False

    def load_next_page(self) -> None:
        """Load the next page of results (paginated mode only).

        No-op when pagination is not active or there are no more pages.
        """
        if self._paginated_loader is None:
            return
        if not self.has_more_pages.value:
            return
        self.loading.value = True
        try:
            result = self._paginated_loader.load_next_page()
            self.assets.value = list(self._paginated_loader.items)
            self.total_count.value = result.total_count
            self.current_page.value = result.page
            self.has_more_pages.value = result.has_more
            self.total_pages.value = result.total_pages
            self.page_loaded.emit(result.page, result.items)
            self.assets_updated.emit(self.assets.value)
        except Exception as exc:
            self._logger.error("Failed to load next page: %s", exc)
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
