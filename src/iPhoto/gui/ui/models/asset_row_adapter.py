"""Adapter for mapping asset rows to Qt model roles."""

from __future__ import annotations

from typing import Dict, Any

from PySide6.QtCore import Qt, QSize

from .roles import Roles
from ..tasks.thumbnail_loader import ThumbnailLoader


class AssetRowAdapter:
    """Helper to retrieve data from asset rows based on Qt roles."""

    def __init__(self, thumb_size: QSize, cache_manager: Any) -> None:
        """Initialize the adapter."""
        self._thumb_size = thumb_size
        self._cache_manager = cache_manager

    def data(self, row: Dict[str, Any], role: int) -> Any:
        """Return the value for the given *row* and *role*."""
        if role == Qt.DisplayRole:
            return ""
        if role == Qt.DecorationRole:
            return self._cache_manager.resolve_thumbnail(row, ThumbnailLoader.Priority.NORMAL)
        if role == Qt.SizeHintRole:
            return QSize(self._thumb_size.width(), self._thumb_size.height())
        if role == Roles.REL:
            return row["rel"]
        if role == Roles.ABS:
            return row["abs"]
        if role == Roles.ASSET_ID:
            return row["id"]
        if role == Roles.IS_IMAGE:
            return row["is_image"]
        if role == Roles.IS_VIDEO:
            return row["is_video"]
        if role == Roles.IS_LIVE:
            return row["is_live"]
        if role == Roles.IS_PANO:
            return row.get("is_pano", False)
        if role == Roles.LIVE_GROUP_ID:
            return row["live_group_id"]
        if role == Roles.LIVE_MOTION_REL:
            return row["live_motion"]
        if role == Roles.LIVE_MOTION_ABS:
            return row["live_motion_abs"]
        if role == Roles.SIZE:
            return row["size"]
        if role == Roles.DT:
            return row["dt"]
        if role == Roles.DT_SORT:
            return row.get("dt_sort", float("-inf"))
        if role == Roles.LOCATION:
            return row.get("location")
        if role == Roles.FEATURED:
            return row["featured"]
        if role == Roles.IS_CURRENT:
            return bool(row.get("is_current", False))
        if role == Roles.IS_SELECTED:
            return bool(row.get("is_selected", False))
        if role == Roles.THUMBNAIL_REV:
            return int(row.get("thumbnail_rev", 0))
        if role == Roles.INFO:
            return dict(row)
        return None
