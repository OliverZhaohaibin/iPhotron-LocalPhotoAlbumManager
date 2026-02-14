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


class GeoAggregatorMixin:
    """Mixin providing geotagged asset collection for LibraryManager."""

    def get_geotagged_assets(self) -> List[GeotaggedAsset]:
        """Return every asset in the library that exposes GPS coordinates.
        
        Uses the single global database at the library root.
        """

        root = self._require_root()
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
            if not isinstance(row, dict):
                continue
            gps = row.get("gps")
            if not isinstance(gps, dict):
                continue
            lat = gps.get("lat")
            lon = gps.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            # ``resolve_location_name`` maps the GPS coordinate to a human-readable
            # label (typically the city) so that low zoom levels can show a
            # meaningful aggregate marker instead of individual thumbnails.
            location_name = resolve_location_name(gps)
            rel = row.get("rel")
            if not isinstance(rel, str) or not rel:
                continue
            abs_path = (root / rel).resolve()
            if abs_path in seen:
                continue
            seen.add(abs_path)
            library_relative_str = rel
            # Compute album_path from the parent directory of the asset
            parent_album_path = row.get("parent_album_path")
            if parent_album_path:
                album_path = root / parent_album_path
                # Compute album-relative path by stripping the parent prefix
                # Use string operations for robustness with paths at album root
                prefix = parent_album_path + "/"
                if rel.startswith(prefix):
                    album_relative_str = rel[len(prefix):]
                elif rel == parent_album_path:
                    # File at the album root with same name as album (edge case)
                    album_relative_str = ""
                else:
                    album_relative_str = Path(rel).name
            else:
                album_path = root
                album_relative_str = rel
            asset_id = str(row.get("id") or rel)
            classified_image, classified_video = classify_media(row)
            # Combine classifier results with any persisted flags to remain
            # compatible with older index rows that stored boolean values.
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
            assets.append(
                GeotaggedAsset(
                    library_relative=library_relative_str,
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
                )
            )

        assets.sort(key=lambda item: item.library_relative)
        return assets
