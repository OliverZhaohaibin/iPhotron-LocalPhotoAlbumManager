"""Async image provider bridging the asset cache to QML."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtQuick import QQuickImageProvider

from ..tasks.thumbnail_loader import ThumbnailLoader

if TYPE_CHECKING:
    from ..models.asset_list_model import AssetListModel

logger = logging.getLogger(__name__)


class ThumbnailProvider(QQuickImageProvider):
    """
    Serves cached thumbnails to QML Image elements.

    Usage in QML: source: "image://thumbnails/" + model.rel

    The provider uses the attached AssetListModel to resolve the 'rel' path
    to a row, and then queries the AssetCacheManager for the pixmap. If the
    pixmap is not in cache, it triggers a background load and returns a
    placeholder.
    """

    def __init__(self, model: AssetListModel) -> None:
        super().__init__(QQuickImageProvider.ImageType.Pixmap)
        self._model = model

    def requestPixmap(self, id: str, size: QSize, requestedSize: QSize) -> QPixmap:
        """
        Handle image requests from QML.

        :param id: The 'rel' path of the asset, optionally followed by query params (e.g. ?v=1)
                   which are stripped by QML engine or need stripping here.
                   Actually QQuickImageProvider receives the string after the scheme.
        """
        # QML might pass query parameters for cache busting, strip them
        rel_path = id
        if "?" in rel_path:
            rel_path = rel_path.split("?", 1)[0]

        # Look up the row in the model
        state_manager = self._model._state_manager
        row_index = state_manager.row_lookup.get(rel_path)

        if row_index is None:
            # Asset not found (maybe filtered out or race condition)
            return self._placeholder(size)

        rows = state_manager.rows
        if not (0 <= row_index < len(rows)):
            return self._placeholder(size)

        row = rows[row_index]

        # Resolve the thumbnail (returns cached pixmap or placeholder + triggers load)
        # We use VISIBLE priority because if QML is requesting it, it's likely on screen
        # (or about to be).
        pixmap = self._model._cache_manager.resolve_thumbnail(
            row, ThumbnailLoader.Priority.VISIBLE
        )

        if pixmap and not pixmap.isNull():
            return pixmap

        return self._placeholder(size)

    def _placeholder(self, size: QSize) -> QPixmap:
        """Return a transparent or solid placeholder."""
        target_size = size if size.isValid() else QSize(512, 512)
        pixmap = QPixmap(target_size)
        pixmap.fill(Qt.transparent)
        return pixmap
