"""QML Image Provider for serving asset thumbnails."""

from __future__ import annotations

import logging
from typing import Dict, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtQuick import QQuickImageProvider

from ..models.asset_cache_manager import AssetCacheManager
from ..tasks.thumbnail_loader import ThumbnailLoader

logger = logging.getLogger(__name__)


class ThumbnailImageProvider(QQuickImageProvider):
    """Bridge between QML image requests and the AssetCacheManager.

    This provider handles requests for ``image://thumbnail/<rel>``.
    It attempts to serve a cached thumbnail immediately. If the thumbnail
    is not in memory, it returns a placeholder and (implicitly, via the model)
    relies on the ``prioritize_rows`` mechanism to trigger the async load.

    Note: The QML GridView logic is responsible for calling ``prioritize_rows``
    based on visibility. This provider is purely for serving the data when
    available.
    """

    def __init__(self, cache_manager: AssetCacheManager) -> None:
        super().__init__(QQuickImageProvider.ImageType.Pixmap)
        self._cache_manager = cache_manager

    def requestPixmap(self, id: str, size: QSize, requestedSize: QSize) -> QPixmap:
        """Handle a request for a thumbnail.

        Parameters
        ----------
        id : str
            The asset relative path (rel) as provided in the URL.
        size : QSize
            Output parameter for the original image size (ignored here).
        requestedSize : QSize
            The target size requested by QML.

        Returns
        -------
        QPixmap
            The cached thumbnail or a placeholder.
        """
        # "id" is the path component after "image://thumbnail/".
        rel = id

        # 1. Try memory cache (fastest)
        pixmap = self._cache_manager.thumbnail_for(rel)
        if pixmap is not None:
            if not requestedSize.isEmpty():
                return pixmap.scaled(
                    requestedSize,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            return pixmap

        # 2. Return transparent placeholder
        # We don't want to block the GUI thread with disk I/O here if possible,
        # and checking disk existence without a full load is race-prone.
        # Since logic is "prioritize visible", the model/view interaction
        # should have already triggered the load. If we are here and it's missing,
        # it means it's still loading.

        # We return a transparent 1x1 pixmap. The delegate in QML handles
        # the "loading" appearance or simply shows the background color.
        # Once the thumbnail loads, the model emits dataChanged (DecorationRole
        # or similar), forcing QML to re-evaluate the source or reload.

        # However, since we use a custom provider URL that doesn't change,
        # QML caches the result of this request. We need to invalidate it.
        # The trick is: The model should append a timestamp/counter to the URL
        # when the thumbnail becomes ready (e.g. image://thumb/path.jpg?v=1).
        # We will handle stripping the query param in the ID if QML passes it,
        # but QQuickImageProvider usually strips query params before passing ID.
        # *Correction*: QQuickImageProvider receives the ID *without* query params
        # if using the standard QML Image element? No, it usually receives the rest of the string.
        # Let's verify standard behavior: usually one uses "image://prov/id".
        # If we change source to "image://prov/id?v=2", QML treats it as a new request.

        # Note: If `id` contains query params, we must strip them to get the rel key.
        if "?" in rel:
            rel = rel.split("?")[0]

        # Re-check cache with stripped key
        pixmap = self._cache_manager.thumbnail_for(rel)
        if pixmap is not None:
             if not requestedSize.isEmpty():
                return pixmap.scaled(
                    requestedSize,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
             return pixmap

        # Return empty/placeholder
        # Ideally we return a placeholder sized to the requested size or standard size
        # to avoid layout shifts if the image element depends on implicit size.
        # But our grid cells are fixed size usually.

        placeholder = QPixmap(1, 1)
        placeholder.fill(Qt.transparent)
        return placeholder
