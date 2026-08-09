"""Helpers for reverse geocoding GPS coordinates."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, Optional

import reverse_geocoder  # type: ignore[import]

from .logging import get_logger


@lru_cache(maxsize=1)
def _geocoder() -> "reverse_geocoder.RGeocoder":
    """Return a cached reverse geocoder instance."""

    return reverse_geocoder.RGeocoder(mode=1, verbose=False)


@lru_cache(maxsize=32768)
def _lookup_location_name(latitude_key: float, longitude_key: float) -> Optional[str]:
    """Resolve a stable location label for the rounded GPS coordinate."""

    try:
        result = _geocoder().query([(latitude_key, longitude_key)])
    except Exception:
        return None

    record: Optional[Dict[str, str]] = None

    def _to_text(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    if isinstance(result, dict):
        record = {key: _to_text(value) for key, value in result.items() if isinstance(key, str)}
    elif isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            record = {
                key: _to_text(value)
                for key, value in first.items()
                if isinstance(key, str)
            }

    if not record:
        return None

    city = str(record.get("name", "")).strip()
    admin = str(record.get("admin2") or record.get("admin1") or "").strip()
    components = [component for component in (city, admin) if component]
    if not components:
        return None
    return " — ".join(components)


def _coerce_coordinate(value: object) -> Optional[float]:
    """Return *value* as decimal coordinates when possible."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return float(candidate)
        except ValueError:
            return None
    return None


def resolve_location_name(gps: Optional[Dict[str, float]]) -> Optional[str]:
    """Return a human readable place name for *gps* coordinates.

    Parameters
    ----------
    gps:
        Mapping containing ``lat``/``lon`` keys (or ``latitude``/``longitude``
        aliases). When either value is missing or the lookup fails the function
        returns ``None``.
    """

    if not gps:
        return None
    latitude = _coerce_coordinate(gps.get("lat"))
    if latitude is None:
        latitude = _coerce_coordinate(gps.get("latitude"))

    longitude = _coerce_coordinate(gps.get("lon"))
    if longitude is None:
        longitude = _coerce_coordinate(gps.get("longitude"))

    if latitude is None or longitude is None:
        return None

    # City/admin names are stable at a much coarser resolution than the raw
    # EXIF GPS coordinates, so rounding dramatically improves cache reuse for
    # burst photos taken in the same area without changing the visible label.
    latitude_key = round(latitude, 4) if math.isfinite(latitude) else latitude
    longitude_key = round(longitude, 4) if math.isfinite(longitude) else longitude
    location_name = _lookup_location_name(latitude_key, longitude_key)
    if location_name is None:
        return None

    get_logger().debug("Resolved location display name: %s", location_name)
    return location_name


__all__ = ["resolve_location_name"]
