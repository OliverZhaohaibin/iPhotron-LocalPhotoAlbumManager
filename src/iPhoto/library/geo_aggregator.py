"""Geotagged asset dataclass and geo-asset collection logic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from ..cache.index_store import get_global_repository
from ..media_classifier import classify_media
from ..utils.geocoding import resolve_location_name
from ..utils.logging import get_logger

if TYPE_CHECKING:
    pass

LOGGER = get_logger()


@dataclass(slots=True, frozen=True)
class GeotaggedAsset:
    """Lightweight descriptor describing an asset with GPS metadata."""

    library_relative: str
    """Relative path from the library root to the asset."""

    album_relative: str
    """Relative path from the asset's album root to the file."""

    absolute_path: Path
    """Absolute filesystem path to the asset."""

    album_path: Path
    """Root directory of the album that owns the asset."""

    asset_id: str
    """Identifier reported by the index row."""

    latitude: float
    longitude: float
    is_image: bool
    is_video: bool
    still_image_time: Optional[float]
    duration: Optional[float]
    location_name: Optional[str]
    """Human-readable label derived from the asset's GPS coordinate."""

    live_photo_group_id: Optional[str]
    """Stable identifier linking the still image to its paired motion asset."""

    live_partner_rel: Optional[str]
    """Library-relative path of the paired motion file when known."""


def geotagged_asset_from_row(root: Path, row: object) -> Optional[GeotaggedAsset]:
    """Return a ``GeotaggedAsset`` converted from one index-store row."""

    if not isinstance(row, dict):
        return None
    gps = row.get("gps")
    if not isinstance(gps, dict):
        return None
    lat = gps.get("lat")
    lon = gps.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None

    rel = row.get("rel")
    if not isinstance(rel, str) or not rel:
        return None

    live_role_raw = row.get("live_role")
    live_role = int(live_role_raw) if isinstance(live_role_raw, (int, float)) else 0
    # Keep only visible rows to mirror regular gallery queries.
    # Hidden motion components (live_role != 0) must not be shown as
    # standalone assets in the location cluster gallery.
    if live_role != 0:
        return None

    abs_path = (root / rel).resolve()
    location_raw = row.get("location")
    if not isinstance(location_raw, str) or not location_raw.strip():
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            location_raw = metadata.get("location") or metadata.get("location_name")
    location_name = (
        str(location_raw).strip()
        if isinstance(location_raw, str) and location_raw.strip()
        else resolve_location_name(gps)
    )

    parent_album_path = row.get("parent_album_path")
    if parent_album_path:
        album_path = root / parent_album_path
        prefix = parent_album_path + "/"
        if rel.startswith(prefix):
            album_relative_str = rel[len(prefix):]
        elif rel == parent_album_path:
            album_relative_str = ""
        else:
            album_relative_str = Path(rel).name
    else:
        album_path = root
        album_relative_str = rel

    asset_id = str(row.get("id") or rel)
    classified_image, classified_video = classify_media(row)
    is_image = classified_image or bool(row.get("is_image"))
    is_video = classified_video or bool(row.get("is_video"))

    still_image_time = row.get("still_image_time")
    if isinstance(still_image_time, (int, float)):
        still_image_value: Optional[float] = float(still_image_time)
    else:
        still_image_value = None

    duration = row.get("dur")
    if isinstance(duration, (int, float)):
        duration_value: Optional[float] = float(duration)
    else:
        duration_value = None

    live_group_raw = row.get("live_photo_group_id")
    live_group_id = (
        str(live_group_raw).strip()
        if isinstance(live_group_raw, str) and live_group_raw.strip()
        else None
    )
    partner_raw = row.get("live_partner_rel")
    live_partner_rel = (
        str(partner_raw).strip()
        if isinstance(partner_raw, str) and partner_raw.strip()
        else None
    )

    return GeotaggedAsset(
        library_relative=rel,
        album_relative=album_relative_str,
        absolute_path=abs_path,
        album_path=album_path,
        asset_id=asset_id,
        latitude=float(lat),
        longitude=float(lon),
        is_image=is_image,
        is_video=is_video,
        still_image_time=still_image_value,
        duration=duration_value,
        location_name=location_name,
        live_photo_group_id=live_group_id,
        live_partner_rel=live_partner_rel,
    )


class GeoAggregatorMixin:
    """Mixin providing geotagged asset collection for LibraryManager."""

    def get_geotagged_assets(self) -> List[GeotaggedAsset]:
        """Return every asset in the library that exposes GPS coordinates.
        
        Uses the single global database at the library root.
        """

        root = self._require_root()
        cached_root = getattr(self, "_geotagged_assets_cache_root", None)
        cached_assets = getattr(self, "_geotagged_assets_cache", None)
        if cached_root == root and cached_assets is not None:
            return list(cached_assets)

        # Track resolved absolute paths we've already yielded. The global
        # index at the library root guarantees uniqueness of library-relative
        # paths, but files may still be reachable via multiple album roots
        # (e.g. via symlinks or hard links), so we deduplicate on absolute
        # paths here for safety.
        seen: set[Path] = set()
        assets: list[GeotaggedAsset] = []

        try:
            # Use single global database at library root
            rows = get_global_repository(root).read_geotagged()
        except Exception:
            return assets

        for row in rows:
            asset = geotagged_asset_from_row(root, row)
            if asset is None:
                continue
            if asset.absolute_path in seen:
                continue
            seen.add(asset.absolute_path)
            assets.append(asset)

        assets.sort(key=lambda item: item.library_relative)
        setattr(self, "_geotagged_assets_cache_root", root)
        setattr(self, "_geotagged_assets_cache", list(assets))
        return assets
