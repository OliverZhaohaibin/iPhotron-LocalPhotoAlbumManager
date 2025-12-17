"""Background worker that assembles asset payloads for the grid views."""

from __future__ import annotations

import logging
import xxhash
from datetime import datetime, timezone
import copy
import os
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from PySide6.QtCore import QObject, QRunnable, Signal, QThread

from ....cache.index_store import IndexStore
from ....config import WORK_DIR_NAME
from ....media_classifier import classify_media
from ....utils.geocoding import resolve_location_name
from ....utils.pathutils import ensure_work_dir
from ....utils.image_loader import qimage_from_bytes


LOGGER = logging.getLogger(__name__)

# Media type constants (matching IndexStore schema and scanner.py)
MEDIA_TYPE_IMAGE = 0
MEDIA_TYPE_VIDEO = 1


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
        - effective_index_root: The path to use for IndexStore initialization
        - album_path: The relative path for filtering, or None for the library root
    """
    if not library_root:
        return root, None

    # Ensure work dir exists at library root
    ensure_work_dir(library_root, WORK_DIR_NAME)

    try:
        root_resolved = root.resolve()
        library_resolved = library_root.resolve()

        if root_resolved == library_resolved:
            # Viewing the library root itself - no album filtering needed
            return library_root, None

        # Compute album path relative to library root
        album_path = root_resolved.relative_to(library_resolved).as_posix()
        return library_root, album_path
    except (ValueError, OSError):
        # If root is not under library_root, fall back to using root as index
        return root, None


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

    width = row.get("w")
    height = row.get("h")
    byte_size = row.get("bytes")

    if not isinstance(width, int) or not isinstance(height, int):
        return False
    if width <= 0 or height <= 0:
        return False
    if not isinstance(byte_size, int) or byte_size <= 1 * 1024 * 1024:
        return False

    aspect_ratio = width / height
    return aspect_ratio >= 2.0


def _is_featured(rel: str, featured: Set[str]) -> bool:
    if rel in featured:
        return True
    live_ref = f"{rel}#live"
    return live_ref in featured


def _parse_timestamp(value: object) -> float:
    """Return a sortable timestamp for ``value``.

    ``index.jsonl`` typically stores capture times as ISO-8601 strings with a trailing
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
    store: Optional[IndexStore] = None,
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
    ensure_work_dir(root, WORK_DIR_NAME)

    params = copy.deepcopy(filter_params) if filter_params else {}
    featured_set = normalize_featured(featured)

    # Determine the effective index root and album filter using helper
    effective_index_root, album_path = compute_album_path(root, library_root)

    store = IndexStore(effective_index_root)
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


class AssetLoaderSignals(QObject):
    """Signal container for :class:`AssetLoaderWorker` events."""

    progressUpdated = Signal(Path, int, int)
    chunkReady = Signal(Path, list)
    finished = Signal(Path, bool)
    error = Signal(Path, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class AssetLoaderWorker(QRunnable):
    """Load album assets on a background thread."""

    def __init__(
        self,
        root: Path,
        featured: Iterable[str],
        signals: AssetLoaderSignals,
        filter_params: Optional[Dict[str, object]] = None,
        library_root: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._root = root
        self._featured: Set[str] = normalize_featured(featured)
        self._signals = signals
        self._is_cancelled = False
        self._filter_params = filter_params
        self._library_root = library_root

    @property
    def root(self) -> Path:
        """Return the album root handled by this worker."""

        return self._root

    @property
    def signals(self) -> AssetLoaderSignals:
        """Expose the worker signals for connection management."""

        return self._signals

    def run(self) -> None:  # pragma: no cover - executed on worker thread
        try:
            QThread.currentThread().setPriority(QThread.LowPriority)
        except Exception:
            pass  # Environment may not support priority changes

        try:
            self._is_cancelled = False
            for chunk in self._build_payload_chunks():
                if self._is_cancelled:
                    break
                if chunk:
                    self._signals.chunkReady.emit(self._root, chunk)
            if not self._is_cancelled:
                self._signals.finished.emit(self._root, True)
            else:
                self._signals.finished.emit(self._root, False)
        except Exception as exc:  # pragma: no cover - surfaced via signal
            if not self._is_cancelled:
                self._signals.error.emit(self._root, str(exc))
            self._signals.finished.emit(self._root, False)

    def cancel(self) -> None:
        """Request cancellation of the current load operation."""

        self._is_cancelled = True

    # ------------------------------------------------------------------
    def _build_payload_chunks(self) -> Iterable[List[Dict[str, object]]]:
        ensure_work_dir(self._root, WORK_DIR_NAME)

        # Determine the effective index root and album path using helper
        effective_index_root, album_path = compute_album_path(self._root, self._library_root)

        store = IndexStore(effective_index_root)

        # Emit indeterminate progress initially
        self._signals.progressUpdated.emit(self._root, 0, 0)

        # Prepare filter params with featured list if needed
        params = copy.deepcopy(self._filter_params) if self._filter_params else {}

        # 2. Stream rows using lightweight geometry-first query
        # Use a transaction context to keep the connection open for both the read and count queries.
        dir_cache: Dict[Path, Optional[Set[str]]] = {}

        def _path_exists(path: Path) -> bool:
            return _cached_path_exists(path, dir_cache)

        with store.transaction():
            generator = store.read_geometry_only(
                filter_params=params,
                sort_by_date=True,
                album_path=album_path,
                include_subalbums=True,
            )

            chunk: List[Dict[str, object]] = []
            last_reported = 0

            # Priority: Emit first 20 items quickly
            first_chunk_size = 20
            normal_chunk_size = 200

            total = 0
            total_calculated = False
            first_batch_emitted = False
            yielded_count = 0

            for position, row in enumerate(generator, start=1):
                # Yield CPU every 50 items to keep UI responsive
                if position % 50 == 0:
                    QThread.msleep(10)

                if self._is_cancelled:
                    return

                # Adjust rel path to be relative to the album root
                adjusted_row = adjust_rel_for_album(row, album_path)

                entry = build_asset_entry(
                    self._root,
                    adjusted_row,
                    self._featured,
                    store,
                    path_exists=_path_exists,
                )

                if entry is not None:
                    chunk.append(entry)

                # Determine emission
                should_flush = False

                if not first_batch_emitted:
                    if len(chunk) >= first_chunk_size:
                        should_flush = True
                        first_batch_emitted = True
                elif len(chunk) >= normal_chunk_size:
                    should_flush = True

                if should_flush:
                    yielded_count += len(chunk)
                    yield chunk
                    chunk = []

                    # Perform count after yielding first chunk
                    if not total_calculated:
                        try:
                            total = store.count(filter_hidden=True, filter_params=params)
                            total_calculated = True
                        except Exception as exc:
                            LOGGER.warning("Failed to count assets in database: %s", exc, exc_info=True)
                            total = 0  # fallback

                # Update progress periodically
                # Use >= total to robustly handle concurrent additions where position might exceed original total
                if total_calculated and (position >= total or position - last_reported >= 50):
                    last_reported = position
                    self._signals.progressUpdated.emit(self._root, position, total)

            if chunk:
                yielded_count += len(chunk)
                yield chunk

            # Final progress update
            if not total_calculated:  # If we never flushed (e.g. small album)
                total = yielded_count
            self._signals.progressUpdated.emit(self._root, total, total)

class LiveIngestWorker(QRunnable):
    """Process in-memory live scan results on a background thread."""

    def __init__(
        self,
        root: Path,
        items: List[Dict[str, object]],
        featured: Iterable[str],
        signals: AssetLoaderSignals,
        filter_params: Optional[Dict[str, object]] = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._root = root
        self._items = items
        self._featured = normalize_featured(featured)
        self._signals = signals
        self._filter_params = filter_params or {}
        self._is_cancelled = False
        self._dir_cache: Dict[Path, Optional[Set[str]]] = {}

    def _path_exists(self, path: Path) -> bool:
        return _cached_path_exists(path, self._dir_cache)

    def _should_include_row(self, row: Dict[str, object]) -> bool:
        """Check if a row should be included based on filter_params.

        This applies the same filter semantics as IndexStore._build_filter_clauses
        but operates on in-memory row dictionaries instead of SQL.

        Key differences from the database filter:
        - For 'favorites': Also checks the featured set, since live scan items
          may not yet have is_favorite persisted in the database.
        """
        filter_mode = self._filter_params.get("filter_mode")
        if not filter_mode:
            return True

        if filter_mode == "videos":
            return row.get("media_type") == MEDIA_TYPE_VIDEO
        elif filter_mode == "live":
            # Live photos have a live_partner_rel set
            return row.get("live_partner_rel") is not None
        elif filter_mode == "favorites":
            # Check the featured set since live items may not have is_favorite set yet
            rel = row.get("rel")
            if rel and rel in self._featured:
                return True
            return bool(row.get("is_favorite"))

        return True

    def cancel(self) -> None:
        """Cancel the current ingest operation."""
        self._is_cancelled = True

    def run(self) -> None:
        try:
            QThread.currentThread().setPriority(QThread.LowPriority)
        except Exception:
            pass  # Environment may not support priority changes

        try:
            chunk: List[Dict[str, object]] = []
            # Batch size to ensure responsiveness and smooth streaming
            batch_size = 50

            for i, row in enumerate(self._items, 1):
                # Yield CPU every batch to allow UI thread to process events
                if i > 0 and i % batch_size == 0:
                    QThread.msleep(10)

                if self._is_cancelled:
                    break

                # Apply filter before processing (skip non-matching items early)
                if not self._should_include_row(row):
                    continue

                # Process the potentially expensive metadata build in the background
                entry = build_asset_entry(self._root, row, self._featured, path_exists=self._path_exists)
                if entry:
                    chunk.append(entry)

                if len(chunk) >= batch_size:
                    self._signals.chunkReady.emit(self._root, list(chunk))
                    chunk = []

            if chunk and not self._is_cancelled:
                self._signals.chunkReady.emit(self._root, chunk)

            if not self._is_cancelled:
                self._signals.finished.emit(self._root, True)
            else:
                self._signals.finished.emit(self._root, False)

        except Exception as exc:
            LOGGER.error("Error processing live items: %s", exc, exc_info=True)
            if not self._is_cancelled:
                self._signals.error.emit(self._root, str(exc))
            self._signals.finished.emit(self._root, False)
