"""Background worker that assembles asset payloads for the grid views."""

from __future__ import annotations

import copy
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import xxhash
from PySide6.QtCore import QObject, QRunnable, Signal, QThread

from ....cache.index_store import IndexStore
from ....config import WORK_DIR_NAME
from ....media_classifier import classify_media
from ....utils.geocoding import resolve_location_name
from ....utils.pathutils import ensure_work_dir
from ....utils.image_loader import qimage_from_bytes


LOGGER = logging.getLogger(__name__)


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
    *,
    skip_existence_check: bool = False,
    skip_location_resolve: bool = False,
    skip_micro_thumbnail: bool = False,
) -> Optional[Dict[str, object]]:
    """Build an asset entry dictionary from a database row.

    Parameters
    ----------
    root : Path
        The album root directory.
    row : Dict[str, object]
        The database row containing asset metadata.
    featured : Set[str]
        Set of featured asset relative paths.
    store : Optional[IndexStore]
        Optional index store for caching location updates.
    path_exists : Optional[Callable[[Path], bool]]
        Optional callable to check if a file exists.
    skip_existence_check : bool
        If True, skip the expensive file existence check. Use for fast initial loading.
    skip_location_resolve : bool
        If True, skip reverse geocoding for location resolution.
    skip_micro_thumbnail : bool
        If True, skip decoding micro thumbnail blobs to QImage.
    """
    rel = str(row.get("rel"))
    if not rel:
        return None

    # Use string concatenation instead of path.resolve() to avoid extra resolution
    # work; we still perform an existence check (with directory-level caching) to
    # drop index rows pointing to files deleted externally.
    abs_path_obj = root / rel
    if not skip_existence_check:
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
    gps_raw = row.get("gps") if isinstance(row, dict) else None
    if not location_name and not skip_location_resolve:
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

    # Decode micro thumbnail if present (can be skipped for faster initial load)
    micro_thumb_img = None
    if not skip_micro_thumbnail:
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

    store = IndexStore(root)
    dir_cache: Dict[Path, Optional[Set[str]]] = {}

    def _path_exists(path: Path) -> bool:
        return _cached_path_exists(path, dir_cache)
    index_rows = list(store.read_geometry_only(
        filter_params=params,
        sort_by_date=True
    ))
    entries: List[Dict[str, object]] = []
    # Filtering for videos, live photos, and favorites is now performed at the database query level
    # via filter_params in store.read_geometry_only, so no post-processing is needed here.
    for row in index_rows:
        entry = build_asset_entry(root, row, featured_set, store, path_exists=_path_exists)
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

    # Page sizes for cursor-based pagination
    # First page uses fast path (no file checks) to show content immediately
    FIRST_PAGE_SIZE = 200
    # Subsequent pages are larger for efficiency
    NORMAL_PAGE_SIZE = 500

    def __init__(
        self,
        root: Path,
        featured: Iterable[str],
        signals: AssetLoaderSignals,
        filter_params: Optional[Dict[str, object]] = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._root = root
        self._featured: Set[str] = normalize_featured(featured)
        self._signals = signals
        self._is_cancelled = False
        self._filter_params = filter_params

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
        store = IndexStore(self._root)

        # Emit indeterminate progress initially
        self._signals.progressUpdated.emit(self._root, 0, 0)

        # Prepare filter params with featured list if needed
        params = copy.deepcopy(self._filter_params) if self._filter_params else {}

        dir_cache: Dict[Path, Optional[Set[str]]] = {}

        def _path_exists(path: Path) -> bool:
            return _cached_path_exists(path, dir_cache)

        # Use paginated loading for better initial responsiveness
        # This avoids loading ALL rows at once and instead fetches in batches

        offset = 0
        yielded_count = 0
        total = 0
        total_calculated = False
        first_batch_emitted = False

        with store.transaction():
            while True:
                if self._is_cancelled:
                    return

                # Determine page size based on whether we've emitted first batch
                page_size = (
                    self.FIRST_PAGE_SIZE if not first_batch_emitted else self.NORMAL_PAGE_SIZE
                )

                # Fetch a page of rows using SQL LIMIT/OFFSET
                rows, has_more = store.read_geometry_paginated(
                    limit=page_size,
                    offset=offset,
                    filter_params=params,
                    sort_by_date=True
                )

                if not rows:
                    break

                # Calculate total count after first page for progress reporting
                # Defer this until after the first chunk is emitted for faster initial display
                if first_batch_emitted and not total_calculated:
                    try:
                        total = store.count(filter_hidden=True, filter_params=params)
                        total_calculated = True
                    except (sqlite3.DatabaseError, OSError) as exc:
                        LOGGER.warning("Failed to count assets in database: %s", exc, exc_info=True)
                        total = 0

                # Process rows into asset entries
                # For the first batch, use fast path: skip existence checks, location resolve,
                # and micro thumbnail decoding to minimize time to first render
                chunk: List[Dict[str, object]] = []
                use_fast_path = not first_batch_emitted

                for row in rows:
                    if self._is_cancelled:
                        return

                    entry = build_asset_entry(
                        self._root,
                        row,
                        self._featured,
                        store,
                        path_exists=_path_exists,
                        skip_existence_check=use_fast_path,
                        skip_location_resolve=use_fast_path,
                        skip_micro_thumbnail=use_fast_path,
                    )

                    if entry is not None:
                        chunk.append(entry)

                if chunk:
                    yielded_count += len(chunk)
                    yield chunk
                    first_batch_emitted = True

                    # Update progress
                    if total_calculated:
                        self._signals.progressUpdated.emit(self._root, yielded_count, total)

                # Move to next page
                offset += len(rows)

                if not has_more:
                    break

                # Yield CPU briefly between pages to keep UI responsive
                QThread.msleep(5)

            # Final progress update
            if not total_calculated:
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
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._root = root
        self._items = items
        self._featured = normalize_featured(featured)
        self._signals = signals
        self._is_cancelled = False
        self._dir_cache: Dict[Path, Optional[Set[str]]] = {}

    def _path_exists(self, path: Path) -> bool:
        return _cached_path_exists(path, self._dir_cache)

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
