"""Service for handling thumbnail cache invalidation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Set

from PySide6.QtCore import QObject, QTimer

if TYPE_CHECKING:
    from ..models.asset_model import AssetModel
    from ..tasks.thumbnail_loader import ThumbnailLoader

_LOGGER = logging.getLogger(__name__)


class ThumbnailCacheService(QObject):
    """Manages scheduling and execution of thumbnail cache invalidation."""

    def __init__(self, asset_model: AssetModel, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._asset_model = asset_model
        # We access thumbnail_loader from asset_model as it holds the shared instance
        self._thumbnail_loader: ThumbnailLoader = asset_model.thumbnail_loader()
        self._pending_thumbnail_refreshes: Set[str] = set()

    def schedule_refresh(self, source: Path) -> None:
        """Refresh thumbnails for *source* on the next event loop turn."""
        metadata = self._asset_model.source_model().metadata_for_absolute_path(source)
        if metadata is None:
            return
        rel_value = metadata.get("rel")
        if not rel_value:
            return
        rel = str(rel_value)
        if rel in self._pending_thumbnail_refreshes:
            return

        def _run_refresh(rel_key: str) -> None:
            try:
                self._refresh_thumbnail_cache_for_rel(rel_key)
            finally:
                self._pending_thumbnail_refreshes.discard(rel_key)

        self._pending_thumbnail_refreshes.add(rel)
        QTimer.singleShot(0, lambda rel_key=rel: _run_refresh(rel_key))

    def _refresh_thumbnail_cache_for_rel(self, rel: str) -> None:
        """Invalidate cached thumbnails identified by *rel*."""
        if not rel:
            return
        source_model = self._asset_model.source_model()
        if hasattr(source_model, "invalidate_thumbnail"):
            source_model.invalidate_thumbnail(rel)
        self._thumbnail_loader.invalidate(rel)
