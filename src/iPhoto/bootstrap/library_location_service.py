"""Library-scoped Location query surface for Maps runtime flows."""

from __future__ import annotations

from pathlib import Path

from ..application.dtos import GeotaggedAsset
from ..application.services.location_asset_service import geotagged_asset_from_row
from .library_asset_query_service import LibraryAssetQueryService


class LibraryLocationService:
    """Own geotagged asset reads for one active library session."""

    def __init__(
        self,
        library_root: Path,
        *,
        query_service: LibraryAssetQueryService | None = None,
    ) -> None:
        self.library_root = Path(library_root)
        self._query_service = query_service or LibraryAssetQueryService(
            self.library_root
        )
        self._geotagged_assets_cache: list[GeotaggedAsset] | None = None

    def list_geotagged_assets(self) -> list[GeotaggedAsset]:
        """Return visible GPS assets, deduplicated and sorted by relative path."""

        if self._geotagged_assets_cache is not None:
            return list(self._geotagged_assets_cache)

        seen: set[Path] = set()
        assets: list[GeotaggedAsset] = []
        try:
            rows = self._query_service.read_geotagged_rows()
        except Exception:
            rows = ()

        for row in rows:
            asset = self.asset_from_row(row)
            if asset is None or asset.absolute_path in seen:
                continue
            seen.add(asset.absolute_path)
            assets.append(asset)

        assets.sort(key=lambda item: item.library_relative)
        self._geotagged_assets_cache = list(assets)
        return list(assets)

    def asset_from_row(self, row: object) -> GeotaggedAsset | None:
        """Convert one index row to a location asset for incremental updates."""

        return geotagged_asset_from_row(self.library_root, row)

    def invalidate_cache(self) -> None:
        """Drop cached map assets."""

        self._geotagged_assets_cache = None


__all__ = ["LibraryLocationService"]
