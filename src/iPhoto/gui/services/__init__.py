"""Lazy exports for GUI workflow services."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AlbumMetadataService": ("album_metadata_service", "AlbumMetadataService"),
    "AssetImportService": ("asset_import_service", "AssetImportService"),
    "AssetMoveService": ("asset_move_service", "AssetMoveService"),
    "DeletionService": ("deletion_service", "DeletionService"),
    "LocationTrashNavigationService": (
        "location_trash_navigation_service",
        "LocationTrashNavigationService",
    ),
    "LibraryUpdateService": ("library_update_service", "LibraryUpdateService"),
    "MoveOperationResult": ("library_update_service", "MoveOperationResult"),
    "PinnedItemsService": ("pinned_items_service", "PinnedItemsService"),
    "PinnedSidebarItem": ("pinned_items_service", "PinnedSidebarItem"),
    "RestorationService": ("restoration_service", "RestorationService"),
    "RestoreBatch": ("restoration_service", "RestoreBatch"),
    "RestoreScheduleResult": ("restoration_service", "RestoreScheduleResult"),
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
