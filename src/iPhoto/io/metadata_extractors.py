"""Shared utility functions and ExifTool extraction helpers for metadata readers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Dict, Optional

from dateutil.parser import isoparse
from dateutil.tz import gettz

from ..utils.logging import get_logger

LOGGER = get_logger()


def _empty_media_info() -> Dict[str, Any]:
    """Return a metadata stub used whenever inspection fails."""

    return {
        "w": None,
        "h": None,
        "mime": None,
        "dt": None,
        "make": None,
        "model": None,
        "lens": None,
        "iso": None,
        "f_number": None,
        "exposure_time": None,
        "exposure_compensation": None,
        "focal_length": None,
        "gps": None,
        "content_id": None,
        "bytes": None,
        "dur": None,
        "codec": None,
        "frame_rate": None,
        "still_image_time": None,
    }


def _normalise_exif_datetime(dt_value: str, exif: Any) -> Optional[str]:
    """Normalise an EXIF ``DateTime`` string to a UTC ISO-8601 representation."""

    fmt = "%Y:%m:%d %H:%M:%S"
    offset_tags = (36880, 36881, 36882)
    offset: Optional[str] = None
    for tag in offset_tags:
        value = exif.get(tag)
        if isinstance(value, str) and value.strip():
            offset = value.strip()
            break

    def _format_result(captured: datetime) -> str:
        return captured.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    if offset:
        if len(offset) == 5 and offset[0] in "+-":
            offset = f"{offset[:3]}:{offset[3:]}"
        combined = f"{dt_value}{offset}"
        try:
            captured = datetime.strptime(combined, f"{fmt}%z")
            return _format_result(captured)
        except ValueError:
            pass

    try:
        naive = datetime.strptime(dt_value, fmt)
    except ValueError:
        return None

    local_tz = gettz() or datetime.now().astimezone().tzinfo or timezone.utc
    return _format_result(naive.replace(tzinfo=local_tz))


def _coerce_decimal(value: Any) -> Optional[float]:
    """Return ``value`` as a floating point number when possible."""

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


def _coerce_fractional(value: Any) -> Optional[float]:
    """Return ``value`` as ``float`` while accepting rational strings."""

    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        candidate = candidate.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
        matches = list(re.finditer(r"-?\d+(?:/\d+|\.\d+)?", candidate))
        if not matches:
            return None

        for match in matches:
            token = match.group(0)
            try:
                if "/" in token:
                    return float(Fraction(token))
                return float(token)
            except (ValueError, ZeroDivisionError):
                continue

        return None
    return None


def _pick_string(*candidates: Any) -> Optional[str]:
    """Return the first non-empty string from ``candidates``."""

    for candidate in candidates:
        if isinstance(candidate, str):
            normalized = candidate.strip()
            if normalized:
                return normalized
    return None


def _extract_group(metadata: Dict[str, Any], group_name: str) -> Optional[Dict[str, Any]]:
    """Return an ExifTool group mapping from either nested or flattened layouts."""

    # ``exiftool`` can emit nested dictionaries (when ``-g1`` is supplied) or a
    # flat mapping of ``Group:Tag`` keys depending on the version and flags in
    # use.  This helper normalises both forms so downstream code can treat the
    # result as a simple dictionary of tag names.
    group = metadata.get(group_name)
    if isinstance(group, dict):
        return group

    prefix = f"{group_name}:"
    extracted = {
        key[len(prefix) :]: value
        for key, value in metadata.items()
        if isinstance(key, str) and key.startswith(prefix)
    }
    return extracted or None


def _extract_gps_from_exiftool(meta: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Extract decimal GPS coordinates from ExifTool's metadata payload."""

    composite = _extract_group(meta, "Composite")
    if composite:
        lat = _coerce_decimal(composite.get("GPSLatitude"))
        lon = _coerce_decimal(composite.get("GPSLongitude"))
        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon}

    quicktime = _extract_group(meta, "QuickTime")
    if quicktime:
        lat = _coerce_decimal(quicktime.get("GPSLatitude"))
        lon = _coerce_decimal(quicktime.get("GPSLongitude"))
        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon}

        iso6709 = quicktime.get("LocationISO6709")
        if isinstance(iso6709, str):
            # Some devices only publish a single ISO 6709 string (for example
            # ``+51.5080-0.1400/``).  The regex extracts the latitude and
            # longitude so they can be converted to floating point numbers.
            match = re.match(r"([+-]\d+\.\d+)([+-]\d+\.\d+)", iso6709)
            if match:
                try:
                    lat, lon = (float(component) for component in match.groups())
                    return {"lat": lat, "lon": lon}
                except (TypeError, ValueError):
                    LOGGER.debug("Failed to parse QuickTime ISO6709 string: %s", iso6709)

    iso6709_fallback = meta.get("com.apple.quicktime.location.ISO6709")
    if isinstance(iso6709_fallback, str):
        match = re.match(r"([+-]\d+\.\d+)([+-]\d+\.\d+)", iso6709_fallback)
        if match:
            try:
                lat, lon = (float(component) for component in match.groups())
                return {"lat": lat, "lon": lon}
            except (TypeError, ValueError):
                LOGGER.debug("Failed to parse QuickTime ISO6709 fallback: %s", iso6709_fallback)

    gps_group = _extract_group(meta, "GPS")
    if gps_group:
        lat = _coerce_decimal(gps_group.get("GPSLatitude"))
        lon = _coerce_decimal(gps_group.get("GPSLongitude"))
        if lat is not None and lon is not None:
            lat_ref = str(gps_group.get("GPSLatitudeRef", "N")).upper()
            lon_ref = str(gps_group.get("GPSLongitudeRef", "E")).upper()
            if lat_ref == "S":
                lat = -lat
            if lon_ref == "W":
                lon = -lon
            return {"lat": lat, "lon": lon}

    return None


def _extract_datetime_from_exiftool(meta: Dict[str, Any]) -> Optional[str]:
    """Extract a UTC ISO-8601 timestamp from the ExifTool metadata payload."""

    composite = _extract_group(meta, "Composite")
    if composite:
        for key in ("SubSecDateTimeOriginal", "SubSecCreateDate", "GPSDateTime"):
            value = composite.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    parsed = isoparse(value)
                except (ValueError, TypeError):
                    continue
                if parsed.tzinfo is None:
                    local_tz = gettz() or datetime.now().astimezone().tzinfo or timezone.utc
                    parsed = parsed.replace(tzinfo=local_tz)
                return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    quicktime = _extract_group(meta, "QuickTime")
    if quicktime:
        for key in ("CreateDate", "ModifyDate"):
            value = quicktime.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                parts = value.split(" ")
                if len(parts) > 1 and ":" in parts[0]:
                    parts[0] = parts[0].replace(":", "-")
                    value = " ".join(parts)
                parsed = isoparse(value)
            except (ValueError, TypeError):
                continue
            if parsed.tzinfo is None:
                local_tz = gettz() or datetime.now().astimezone().tzinfo or timezone.utc
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    exif_ifd = _extract_group(meta, "ExifIFD")
    if exif_ifd:
        for key in ("DateTimeOriginal", "CreateDate"):
            value = exif_ifd.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                parsed = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                continue

            offset_str = exif_ifd.get("OffsetTimeOriginal")
            tz_info = None
            if isinstance(offset_str, str) and offset_str:
                try:
                    tz_info = datetime.strptime(offset_str, "%z").tzinfo
                except ValueError:
                    tz_info = None

            if tz_info is None:
                tz_info = gettz() or datetime.now().astimezone().tzinfo or timezone.utc

            parsed = parsed.replace(tzinfo=tz_info)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    return None


def _extract_content_id_from_exiftool(meta: Dict[str, Any]) -> Optional[str]:
    """Extract the Apple ``ContentIdentifier`` used for Live Photo pairing.

    Different ExifTool builds/platforms expose this tag in different groups or
    flattened key forms (e.g. ``Keys:ContentIdentifier`` or
    ``com.apple.quicktime.content.identifier``). We therefore probe common
    groups first, then fall back to scanning flat keys.
    """

    def _pick(mapping: Dict[str, Any], *names: str) -> Optional[str]:
        for name in names:
            value = mapping.get(name)
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    return normalized
        return None

    for group_name in ("Apple", "QuickTime", "Keys", "ItemList", "XMP"):
        group = _extract_group(meta, group_name)
        if group:
            content_id = _pick(
                group,
                "ContentIdentifier",
                "ContentIdentifierUUID",
                "com.apple.quicktime.content.identifier",
            )
            if content_id:
                return content_id

    for key, value in meta.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        normalized_key = key.strip().lower().replace("_", "")
        if not normalized_key:
            continue
        if (
            normalized_key.endswith(":contentidentifier")
            or normalized_key.endswith(":contentidentifieruuid")
            or normalized_key.endswith(".content.identifier")
            or normalized_key.endswith("contentidentifier")
        ):
            content_id = value.strip()
            if content_id:
                return content_id

    return None
