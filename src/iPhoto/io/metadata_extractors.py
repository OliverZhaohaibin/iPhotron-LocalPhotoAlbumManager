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


# Matches the raw EXIF LensInfo / LensSpecification 4-value tuple that ExifTool
# sometimes emits verbatim as a string, e.g. "23 23 2 2" or "24/1 70/1 28/10 28/10".
# Format: <min_focal> <max_focal> <min_fnumber> <max_fnumber>
# Each value is an integer, a decimal, or a rational fraction (N/D).
_RAW_LENS_INFO_RE = re.compile(
    r"^(?P<fl_min>\d+(?:/\d+|\.\d+)?)"
    r"\s+(?P<fl_max>\d+(?:/\d+|\.\d+)?)"
    r"\s+(?P<fn_min>\d+(?:/\d+|\.\d+)?)"
    r"\s+(?P<fn_max>\d+(?:/\d+|\.\d+)?)$"
)
# A focal-length value is displayed as an integer when it rounds to within this
# fractional distance of the nearest whole number (e.g. 23.04 → "23", not "23.0").
_LENS_INT_DISPLAY_THRESHOLD = 0.05
# Minimum difference in mm between min and max focal length that classifies a
# lens as a zoom rather than a prime (handles floating-point rounding for primes
# whose min == max, e.g. "23 23").
_ZOOM_FOCAL_THRESHOLD = 0.5
# Minimum difference in f-number between wide-open at the short and long ends
# that classifies a zoom as variable-aperture (e.g. f/3.5–5.6 vs f/2.8–2.8).
_VARIABLE_APERTURE_THRESHOLD = 0.1


def _normalise_lens_value(value: str) -> str:
    """Convert a raw EXIF LensInfo spec string to a human-readable form.

    If *value* looks like a raw 4-value LensInfo tuple (e.g. ``"23 23 2 2"``
    or ``"24/1 70/1 28/10 28/10"``) it is reformatted as ``"23mm f/2"`` or
    ``"24-70mm f/2.8"``.  Any other string is returned unchanged.
    """

    m = _RAW_LENS_INFO_RE.match(value.strip())
    if not m:
        return value

    def _parse(token: str) -> Optional[float]:
        try:
            return float(Fraction(token)) if "/" in token else float(token)
        except (ValueError, ZeroDivisionError):
            return None

    fl_min = _parse(m.group("fl_min"))
    fl_max = _parse(m.group("fl_max"))
    fn_min = _parse(m.group("fn_min"))
    fn_max = _parse(m.group("fn_max"))

    if fl_min is None or fl_min <= 0:
        return value

    def _fmt(v: float) -> str:
        return str(int(round(v))) if abs(v - round(v)) < _LENS_INT_DISPLAY_THRESHOLD else f"{v:.1f}"

    if fl_max is not None and abs(fl_min - fl_max) > _ZOOM_FOCAL_THRESHOLD:
        focal_str = f"{_fmt(fl_min)}-{_fmt(fl_max)}mm"
    else:
        focal_str = f"{_fmt(fl_min)}mm"

    if fn_min is None or fn_min <= 0:
        return focal_str

    if fn_max is not None and fn_max > 0 and abs(fn_min - fn_max) > _VARIABLE_APERTURE_THRESHOLD:
        aperture_str = f"f/{_fmt(fn_min)}-{_fmt(fn_max)}"
    else:
        aperture_str = f"f/{_fmt(fn_min)}"

    return f"{focal_str} {aperture_str}"


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


def _parse_duration_value(value: Any) -> Optional[float]:
    """Parse a duration from various formats into seconds.

    Accepted formats:
    * Numeric ``int`` / ``float`` – returned directly.
    * Plain numeric string – ``"8.01"``
    * Seconds with unit suffix – ``"8.01 s"``
    * Matroska / ffprobe HH:MM:SS – ``"00:01:23.456"`` or ``"00:01:23.456000000"``
    """

    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if not candidate:
        return None

    # Strip a trailing "s" unit that ExifTool sometimes appends.
    if candidate.lower().endswith(" s"):
        candidate = candidate[:-2].strip()

    # Try plain float first (covers "8.01" and "123.456000").
    try:
        result = float(candidate)
        return result if result > 0 else None
    except ValueError:
        pass

    # Try HH:MM:SS(.fraction) (Matroska DURATION tag).
    match = re.match(
        r"(\d+):(\d{2}):(\d{2})(?:\.(\d+))?$",
        candidate,
    )
    if match:
        hours, minutes, seconds = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if minutes > 59 or seconds > 59:
            return None
        frac = float(f"0.{match.group(4)}") if match.group(4) else 0.0
        total = hours * 3600 + minutes * 60 + seconds + frac
        return total if total > 0 else None

    return None


def _extract_duration_from_exiftool(meta: Dict[str, Any]) -> Optional[float]:
    """Extract video duration (in seconds) from ExifTool metadata.

    ExifTool exposes duration in several groups depending on the container
    format (QuickTime, Matroska, AVI, etc.) and build.  Values may be plain
    numbers, suffixed with ``" s"``, or formatted as ``HH:MM:SS``.
    """

    quicktime = _extract_group(meta, "QuickTime")
    if quicktime:
        # ``Duration`` is the standard tag.  ``MovieHeaderDuration`` appears in
        # QuickTime MOV files; ``TrackDuration`` is used for individual tracks
        # and is checked as a last resort when the container-level tags are
        # absent (common with AVI wrappers on Linux ExifTool builds).
        for key in ("Duration", "MovieHeaderDuration", "TrackDuration"):
            dur = _parse_duration_value(quicktime.get(key))
            if dur is not None:
                return dur

    composite = _extract_group(meta, "Composite")
    if composite:
        dur = _parse_duration_value(composite.get("Duration"))
        if dur is not None:
            return dur

    # Some ExifTool builds emit a flat ``Composite:Duration`` key.
    for key, value in meta.items():
        if not isinstance(key, str):
            continue
        lower_key = key.lower()
        if lower_key in ("composite:duration", "quicktime:duration"):
            dur = _parse_duration_value(value)
            if dur is not None:
                return dur

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
