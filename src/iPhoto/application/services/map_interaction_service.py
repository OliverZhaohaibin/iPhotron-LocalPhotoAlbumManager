"""Application-level routing rules for map marker interactions."""

from __future__ import annotations

from collections.abc import Iterable

from ..dtos import GeotaggedAsset, MapMarkerActivation


class LibraryMapInteractionService:
    """Translate raw marker payloads into application navigation decisions."""

    def activate_marker_assets(self, assets: object) -> MapMarkerActivation:
        normalized = self._normalize_assets(assets)
        if not normalized:
            return MapMarkerActivation(kind="none")
        if len(normalized) == 1:
            return MapMarkerActivation(
                kind="asset",
                asset_relative=normalized[0].library_relative,
                assets=tuple(normalized),
            )
        return MapMarkerActivation(kind="cluster", assets=tuple(normalized))

    def _normalize_assets(self, assets: object) -> list[GeotaggedAsset]:
        if isinstance(assets, GeotaggedAsset):
            return [assets]
        if not isinstance(assets, Iterable):
            return []
        return [asset for asset in assets if isinstance(asset, GeotaggedAsset)]


__all__ = ["LibraryMapInteractionService"]
