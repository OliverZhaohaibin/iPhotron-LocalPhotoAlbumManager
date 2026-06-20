"""Lazy compatibility exports for reusable GUI widgets.

Importing any concrete widget module necessarily executes this package file.
Keeping it free of eager imports prevents a basic sidebar import from loading
map, multimedia, editing, People, and GPU feature trees before first paint.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AlbumSidebar": ("album_sidebar", "AlbumSidebar"),
    "AssetGridDelegate": ("asset_delegate", "AssetGridDelegate"),
    "AssetGrid": ("asset_grid", "AssetGrid"),
    "GalleryGridView": ("gallery_grid_view", "GalleryGridView"),
    "ChromeStatusBar": ("chrome_status_bar", "ChromeStatusBar"),
    "CustomTitleBar": ("custom_title_bar", "CustomTitleBar"),
    "DetailPageWidget": ("detail_page", "DetailPageWidget"),
    "FilmstripView": ("filmstrip_view", "FilmstripView"),
    "ImageViewer": ("image_viewer", "ImageViewer"),
    "EditSidebar": ("edit_sidebar", "EditSidebar"),
    "FaceNameOverlayWidget": ("face_name_overlay", "FaceNameOverlayWidget"),
    "GalleryPageWidget": ("gallery_page", "GalleryPageWidget"),
    "InfoPanel": ("info_panel", "InfoPanel"),
    "InformationPopup": ("information_popup", "InformationPopup"),
    "MainHeaderWidget": ("main_header", "MainHeaderWidget"),
    "PlayerBar": ("player_bar", "PlayerBar"),
    "VideoArea": ("video_area", "VideoArea"),
    "VideoTrimBar": ("video_trim_bar", "VideoTrimBar"),
    "PreviewWindow": ("preview_window", "PreviewWindow"),
    "PhotoMapView": ("photo_map_view", "PhotoMapView"),
    "LiveBadge": ("live_badge", "LiveBadge"),
    "NotificationToast": ("notification_toast", "NotificationToast"),
    "PeopleDashboardWidget": ("people_dashboard", "PeopleDashboardWidget"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value
