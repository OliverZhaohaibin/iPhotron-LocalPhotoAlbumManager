"""Utility functions and constants for asset loading."""

from __future__ import annotations

import logging
import sqlite3
import xxhash
from datetime import datetime, timezone
import copy
import os
import re
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple, TYPE_CHECKING

from ....cache.index_store import get_global_repository
from ....config import RECENTLY_DELETED_DIR_NAME
from ....media_classifier import classify_media
from ....utils.geocoding import resolve_location_name
from ....utils.pathutils import ensure_work_dir
from ....utils.image_loader import qimage_from_bytes

if TYPE_CHECKING:
    from ....cache.index_store.repository import AssetRepository

LOGGER = logging.getLogger(__name__)

# Media type constants (matching repository schema and scanner.py)
MEDIA_TYPE_IMAGE = 0
MEDIA_TYPE_VIDEO = 1

THUMBNAIL_SUFFIX_RE = re.compile(r"_(\d{2,4})x(\d{2,4})(?=\.[^.]+$)")
THUMBNAIL_MAX_DIMENSION = 512
THUMBNAIL_MAX_BYTES = 350_000


def compute_album_path(
    root: Path, library_root: Optional[Path]
) -> Tuple[Path, Optional[str]]:
    """Compute the effective index root and album path for global index filtering.

    When a library_root is provided, this function determines:
    1. The effective index root (library_root for global index, or root as fallback)
    2. The album_path relative to library_root for filtering assets

    Args:
        root: The album root directory being loaded.
        library_root: The library root where the global index resides, or None.

    Returns:
        A tuple of (effective_index_root, album_path) where:
        - effective_index_root: The path to use for repository initialization
        - album_path: The relative path for filtering, or None for the library root
    """
    if not library_root:
        return root, None

    # Ensure work dir exists at library root
    ensure_work_dir(library_root)

    # Prefer robust, case-tolerant relative computation to avoid dropping
    # album filters (which would leak All Photos into physical album views).
    try:
        rel = Path(os.path.relpath(root, library_root)).as_posix()
    except (ValueError, OSError):
        rel = None

    if rel is None or rel.startswith(".."):
        # Outside the library – fall back to per-folder index
        return root, None

    if rel == ".":
        # Library root
        return library_root, None

    # Debug trace to diagnose album filtering issues
    LOGGER.debug(
        "asset_loader.compute_album_path root=%s library_root=%s album_path=%s",
        root,
        library_root,
        rel,
    )

    return library_root, rel


def adjust_rel_for_album(row: Dict[str, object], album_path: Optional[str]) -> Dict[str, object]:
    """Adjust the rel path in a row to be relative to the album root.

    When loading assets from the global index with album filtering, the rel paths
    are library-relative (e.g., "Album1/photo.jpg"). This function strips the
    album_path prefix to make them relative to the album root (e.g., "photo.jpg").

    Args:
        row: The asset row from the database.
        album_path: The album path prefix to strip, or None if no adjustment needed.

    Returns:
        The original row if no adjustment needed, or a copy with adjusted rel path.
    """
    if not album_path:
        return row

    rel = row.get("rel")
    if not rel:
        return row

    rel_str = str(rel)
    prefix = album_path + "/"
    if rel_str.startswith(prefix):
        adjusted_row = dict(row)  # Don't modify original row
        adjusted_row["rel"] = rel_str[len(prefix):]
        return adjusted_row

    return row


def normalize_featured(featured: Iterable[str]) -> Set[str]:
    return {str(entry) for entry in featured}


def _determine_size(row: Dict[str, object], is_image: bool) -> object:
    if is_image:
        return (row.get("w"), row.get("h"))
    return {"bytes": row.get("bytes"), "duration": row.get("dur")}


def _is_thumbnail_candidate(rel: str, row: Dict[str, object], is_image: bool) -> bool:
    if not is_image:
        return False
    match = THUMBNAIL_SUFFIX_RE.search(Path(rel).name)
    if not match:
        return False
    try:
        width = int(match.group(1))
        height = int(match.group(2))
    except ValueError:
        return False
    row_w = row.get("w")
    row_h = row.get("h")
    if isinstance(row_w, (int, float)) and isinstance(row_h, (int, float)):
        if int(row_w) != width or int(row_h) != height:
            return False
    if max(width, height) > THUMBNAIL_MAX_DIMENSION:
        return False
    size_bytes = row.get("bytes")
    if isinstance(size_bytes, (int, float)) and int(size_bytes) > THUMBNAIL_MAX_BYTES:
        return False
    return True


def _is_panorama_candidate(row: Dict[str, object], is_image: bool) -> bool:
    """Return ``True`` when *row* looks like a panorama photograph.

    The heuristic is intentionally conservative: it only flags assets that are
    confirmed still images, have a wide aspect ratio (width at least twice the
    height), and exceed a minimum size threshold. The size gate helps filter out
    tiny thumbnails or preview files that might also be wide but should not
    display the panorama badge.
    """

    if not is_image:
        return False

    def _coerce_positive_number(value: object) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value) if value > 0 else None
        if isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                return None
            return parsed if parsed > 0 else None
        return None

    width = _coerce_positive_number(row.get("w"))
    height = _coerce_positive_number(row.get("h"))
    aspect_ratio = _coerce_positive_number(row.get("aspect_ratio"))
    byte_size = _coerce_positive_number(row.get("bytes"))

    if width is not None and height is not None:
        aspect_ratio = width / height
    if aspect_ratio is None:
        return False

    if byte_size is not None and byte_size <= 1 * 1024 * 1024:
        return False

    return aspect_ratio >= 2.0


def _is_featured(rel: str, featured: Set[str]) -> bool:
    if rel in featured:
        return True
    live_ref = f"{rel}#live"
    return live_ref in featured


def _parse_timestamp(value: object) -> float:
    """Return a sortable timestamp for ``value``.

    ``global_index.db`` typically stores capture times as ISO-8601 strings with a trailing
    ``Z``, but this helper also accepts ISO-8601 strings without the trailing ``Z``.
    The helper normalises the representation and falls back to
    ``-inf`` for missing or unparsable values so assets without metadata sort
    to the end of descending views.
    """

    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return float("-inf")
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            stamp = datetime.fromisoformat(normalized)
        except ValueError:
            return float("-inf")
    else:
        return float("-inf")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    try:
        return stamp.timestamp()
    except OSError:  # pragma: no cover - out-of-range timestamp on platform
        return float("-inf")


# Maximum entries to cache per directory when checking on-disk presence.
# Avoid caching very large directories to prevent high memory usage.
DIR_CACHE_THRESHOLD = 1000
def _path_exists_direct(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _cached_path_exists(path: Path, cache: Dict[Path, Optional[Set[str]]]) -> bool:
    parent = path.parent
    names = cache.get(parent)
    if names is None:
        try:
            names = set()
            for idx, entry in enumerate(os.scandir(parent), start=1):
                names.add(entry.name)
                if idx > DIR_CACHE_THRESHOLD:
                    # Avoid holding huge directory listings; fall back to direct exists checks.
                    cache[parent] = None
                    return _path_exists_direct(path)
        except OSError:
            names = set()
        cache[parent] = names
    if names is None:
        return _path_exists_direct(path)
    return path.name in names


def build_asset_entry(
    root: Path,
    row: Dict[str, object],
    featured: Set[str],
    store: Optional["AssetRepository"] = None,
    path_exists: Optional[Callable[[Path], bool]] = None,
) -> Optional[Dict[str, object]]:
    rel = str(row.get("rel"))
    if not rel:
        return None

    # Use string concatenation instead of path.resolve() to avoid extra resolution
    # work; we still perform an existence check (with directory-level caching) to
    # drop index rows pointing to files deleted externally.
    abs_path_obj = root / rel
    exists_fn = path_exists or _path_exists_direct
    if not exists_fn(abs_path_obj):
        return None
    abs_path = str(abs_path_obj)

    is_image, is_video = classify_media(row)
    if is_video and row.get("live_partner_rel"):
        return None
    if _is_thumbnail_candidate(rel, row, is_image):
        return None
    is_pano = _is_panorama_candidate(row, is_image)

    live_partner_rel = row.get("live_partner_rel")
    live_motion: Optional[str] = None
    live_motion_abs: Optional[str] = None
    live_group_id: Optional[str] = None

    if isinstance(live_partner_rel, str) and live_partner_rel:
        live_motion = live_partner_rel
        live_motion_abs = str(root / live_partner_rel)
        # Use robust 64-bit hash to prevent collisions in large libraries
        combined_key = f"{rel}|{live_partner_rel}".encode("utf-8")
        live_group_id = f"live_{xxhash.xxh64(combined_key).hexdigest()}"

    # Use cached location if available, otherwise resolve and optionally cache it
    location_name = row.get("location")
    gps_raw = None
    if not location_name:
        gps_raw = row.get("gps") if isinstance(row, dict) else None
        if gps_raw:
            location_name = resolve_location_name(gps_raw)
            if location_name and store:
                try:
                    store.update_location(rel, location_name)
                except sqlite3.OperationalError:
                    LOGGER.debug(
                        "Skipping location cache update for %s due to locked database",
                        rel,
                    )
                except Exception:
                    # Log write failures during read operations to aid debugging, but do not crash
                    LOGGER.warning(
                        "Failed to update location cache for asset '%s': %s",
                        rel, location_name, exc_info=True
                    )
    else:
        # Always extract gps_raw so it can be included in the entry dictionary.
        gps_raw = row.get("gps") if isinstance(row, dict) else None

    # Resolve timestamp with legacy fallback safety
    ts_value = -1
    ts_raw = row.get("ts")
    if ts_raw is not None:
        ts_value = int(ts_raw)
    else:
        # Fallback for legacy rows: parse 'dt' on the fly.
        dt_parsed = _parse_timestamp(row.get("dt"))
        if dt_parsed != float("-inf"):
            ts_value = int(dt_parsed * 1_000_000)

    # Eagerly decode micro thumbnail if present
    micro_thumb_img = None
    micro_thumb_blob = row.get("micro_thumbnail")
    if isinstance(micro_thumb_blob, bytes):
        micro_thumb_img = qimage_from_bytes(micro_thumb_blob)

    entry: Dict[str, object] = {
        "rel": rel,
        "abs": abs_path,
        "id": row.get("id", rel),
        "name": Path(rel).name,
        "is_current": False,
        "is_image": is_image,
        "is_video": is_video,
        "is_live": bool(live_motion),
        "is_pano": is_pano,
        "live_group_id": live_group_id,
        "live_motion": live_motion,
        "live_motion_abs": live_motion_abs,
        "size": _determine_size(row, is_image),
        "dt": row.get("dt"),
        "dt_sort": _parse_timestamp(row.get("dt")),
        "ts": ts_value,
        "featured": bool(row.get("is_favorite")) or _is_featured(rel, featured),
        "still_image_time": row.get("still_image_time"),
        "dur": row.get("dur"),
        "location": location_name,
        "gps": gps_raw,
        "bytes": row.get("bytes"),
        "mime": row.get("mime"),
        "make": row.get("make"),
        "model": row.get("model"),
        "lens": row.get("lens"),
        "iso": row.get("iso"),
        "f_number": row.get("f_number"),
        "exposure_time": row.get("exposure_time"),
        "exposure_compensation": row.get("exposure_compensation"),
        "focal_length": row.get("focal_length"),
        "w": row.get("w"),
        "h": row.get("h"),
        "content_id": row.get("content_id"),
        "frame_rate": row.get("frame_rate"),
        "codec": row.get("codec"),
        "original_rel_path": row.get("original_rel_path"),
        "original_album_id": row.get("original_album_id"),
        "original_album_subpath": row.get("original_album_subpath"),
        "micro_thumbnail_image": micro_thumb_img,
    }
    return entry


def compute_asset_rows(
    root: Path,
    featured: Iterable[str],
    filter_params: Optional[Dict[str, object]] = None,
    library_root: Optional[Path] = None,
) -> Tuple[List[Dict[str, object]], int]:
    """
    Assemble asset entries for grid views, applying optional filtering.

    Parameters
    ----------
    root : Path
        The root directory containing the asset index and media files.
    featured : Iterable[str]
        An iterable of asset relative paths to be marked as featured.
    filter_params : Optional[Dict[str, object]], optional
        Dictionary of filter parameters to restrict the returned assets.
        Valid keys include:
            - 'filter_mode': str, one of 'all', 'images', 'videos', 'featured'.
              Determines which asset types are included.
            - Additional keys may be supported by the index store for filtering.
        If None or empty, no filtering is applied.
    library_root : Optional[Path], optional
        The root directory of the library. If provided, uses the global index at
        library_root and filters by album path. If None, uses root for the index.

    Returns
    -------
    entries : List[Dict[str, object]]
        List of asset entry dictionaries suitable for grid display.
    count : int
        The number of entries returned.
    """
    ensure_work_dir(root)

    params = copy.deepcopy(filter_params) if filter_params else {}
    featured_set = normalize_featured(featured)

    # Determine the effective index root and album filter using helper
    effective_index_root, album_path = compute_album_path(root, library_root)

    if album_path is None and library_root is not None:
        params.setdefault("exclude_path_prefix", RECENTLY_DELETED_DIR_NAME)

    store = get_global_repository(effective_index_root)
    dir_cache: Dict[Path, Optional[Set[str]]] = {}

    def _path_exists(path: Path) -> bool:
        return _cached_path_exists(path, dir_cache)

    index_rows = list(store.read_geometry_only(
        filter_params=params,
        sort_by_date=True,
        album_path=album_path,
        include_subalbums=True,
    ))
    entries: List[Dict[str, object]] = []
    # Filtering for videos, live photos, and favorites is now performed at the database query level
    # via filter_params in store.read_geometry_only, so no post-processing is needed here.
    for row in index_rows:
        # Adjust rel path to be relative to the album root
        adjusted_row = adjust_rel_for_album(row, album_path)
        entry = build_asset_entry(root, adjusted_row, featured_set, store, path_exists=_path_exists)
        if entry is not None:
            entries.append(entry)
    return entries, len(entries)


def _safe_signal_emit(signal_func: Callable, *args) -> bool:
    """Safely emit a Qt signal, handling deleted signal sources gracefully.
    
    During rapid album switching, background workers may still be running when
    their signal objects are deleted. This helper prevents RuntimeError crashes
    by gracefully handling signal source deletion.
    
    Args:
        signal_func: The signal's emit method (e.g., self._signals.chunkReady.emit)
        *args: Arguments to pass to the signal
        
    Returns:
        True if the signal was emitted successfully, False if the signal source
        was deleted (indicating the worker should stop processing).
    """
    try:
        signal_func(*args)
        return True
    except RuntimeError:
        # Signal source has been deleted - this is expected during rapid switching
        return False
