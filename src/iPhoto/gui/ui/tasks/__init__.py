"""Lazy background-task exports for startup-safe targeted imports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AssetLoaderWorker": ("asset_loader_worker", "AssetLoaderWorker"),
    "EditSidebarPreviewWorker": ("edit_sidebar_preview_worker", "EditSidebarPreviewWorker"),
    "InfoPanelMetadataWorker": ("info_panel_metadata_worker", "InfoPanelMetadataWorker"),
    "ImportSignals": ("import_worker", "ImportSignals"),
    "ImportWorker": ("import_worker", "ImportWorker"),
    "IncrementalRefreshSignals": ("incremental_refresh_worker", "IncrementalRefreshSignals"),
    "IncrementalRefreshWorker": ("incremental_refresh_worker", "IncrementalRefreshWorker"),
    "MoveSignals": ("move_worker", "MoveSignals"),
    "MoveWorker": ("move_worker", "MoveWorker"),
    "ThumbnailGeneratorWorker": ("thumbnail_generator_worker", "ThumbnailGeneratorWorker"),
    "ThumbnailLoader": ("thumbnail_loader", "ThumbnailLoader"),
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
