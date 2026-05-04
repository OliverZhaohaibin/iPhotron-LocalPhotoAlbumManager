"""Pure helpers for location asset query surfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..dtos import GeotaggedAsset
from ...media_classifier import classify_media
from ...utils.geocoding import resolve_location_name


def geotagged_asset_from_row(
    root: Path,
    row: object,
) -> GeotaggedAsset | None:
    """Return a geotagged asset converted from one index-store row."""

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
    if live_role != 0:
        return None

    library_root = Path(root)
    abs_path = (library_root / rel).resolve()
    location_raw: Any = row.get("location")
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
        album_path = library_root / str(parent_album_path)
        prefix = str(parent_album_path) + "/"
        if rel.startswith(prefix):
            album_relative_str = rel[len(prefix):]
        elif rel == parent_album_path:
            album_relative_str = ""
        else:
            album_relative_str = Path(rel).name
    else:
        album_path = library_root
        album_relative_str = rel

    asset_id = str(row.get("id") or rel)
    classified_image, classified_video = classify_media(row)
    is_image = classified_image or bool(row.get("is_image"))
    is_video = classified_video or bool(row.get("is_video"))

    still_image_time = row.get("still_image_time")
    if isinstance(still_image_time, (int, float)):
        still_image_value: float | None = float(still_image_time)
    else:
        still_image_value = None

    duration = row.get("dur")
    if isinstance(duration, (int, float)):
        duration_value: float | None = float(duration)
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


__all__ = ["geotagged_asset_from_row"]
