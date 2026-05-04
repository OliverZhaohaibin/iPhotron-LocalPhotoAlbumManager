"""Compatibility entry points for geotagged asset collection."""

from __future__ import annotations

from pathlib import Path
from typing import List

from ..application.dtos import GeotaggedAsset
from ..application.services.location_asset_service import geotagged_asset_from_row
from ..bootstrap.library_location_service import LibraryLocationService
from ..utils.geocoding import resolve_location_name  # compatibility patch target


class GeoAggregatorMixin:
    """Mixin preserving the LibraryRuntimeController geotagged asset API."""

    def get_geotagged_assets(self) -> List[GeotaggedAsset]:
        """Return every visible asset in the library with GPS coordinates."""

        location_service = getattr(self, "location_service", None)
        active_session = getattr(self, "library_session", None)
        default_location_service = (
            active_session.locations if active_session is not None else None
        )
        list_geotagged_assets = getattr(
            location_service,
            "list_geotagged_assets",
            None,
        )
        if (
            callable(list_geotagged_assets)
            and location_service is not None
            and location_service is not default_location_service
        ):
            return list(list_geotagged_assets())

        root = self._require_root()
        cached_root = getattr(self, "_geotagged_assets_cache_root", None)
        cached_assets = getattr(self, "_geotagged_assets_cache", None)
        if cached_root == root and cached_assets is not None:
            return list(cached_assets)

        query_service = getattr(self, "asset_query_service", None)
        if query_service is None:
            return []
        service = LibraryLocationService(root, query_service=query_service)
        assets = service.list_geotagged_assets()
        setattr(self, "_geotagged_assets_cache_root", root)
        setattr(self, "_geotagged_assets_cache", list(assets))
        return list(assets)


__all__ = [
    "GeoAggregatorMixin",
    "GeotaggedAsset",
    "geotagged_asset_from_row",
    "resolve_location_name",
]
