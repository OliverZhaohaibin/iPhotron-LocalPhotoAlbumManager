"""Background worker that assembles asset payloads for the grid views."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from PySide6.QtCore import QObject, QRunnable, Signal, QThread

from ....bootstrap.library_asset_query_service import LibraryAssetQueryService
from ....config import RECENTLY_DELETED_DIR_NAME
from ....core.pairing import pair_live
from ....utils.pathutils import ensure_work_dir

from .asset_loader_utils import (
    MEDIA_TYPE_IMAGE,
    MEDIA_TYPE_VIDEO,
    THUMBNAIL_SUFFIX_RE,
    THUMBNAIL_MAX_DIMENSION,
    THUMBNAIL_MAX_BYTES,
    compute_album_path,
    normalize_featured,
    build_asset_entry,
    compute_asset_rows,
    _safe_signal_emit,
    _cached_path_exists,
    _parse_timestamp,
    _determine_size,
    _is_thumbnail_candidate,
    _is_panorama_candidate,
    _is_featured,
    require_query_service,
    DIR_CACHE_THRESHOLD,
    _path_exists_direct,
)

LOGGER = logging.getLogger(__name__)


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
        asset_query_service: LibraryAssetQueryService | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._root = root
        self._featured: Set[str] = normalize_featured(featured)
        self._signals = signals
        self._is_cancelled = False
        self._filter_params = filter_params
        self._library_root = library_root
        self._asset_query_service = asset_query_service

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
                    if not _safe_signal_emit(self._signals.chunkReady.emit, self._root, chunk):
                        return  # Signal source deleted, stop processing
            if not self._is_cancelled:
                _safe_signal_emit(self._signals.finished.emit, self._root, True)
            else:
                _safe_signal_emit(self._signals.finished.emit, self._root, False)
        except Exception as exc:  # pragma: no cover - surfaced via signal
            if not self._is_cancelled:
                _safe_signal_emit(self._signals.error.emit, self._root, str(exc))
            _safe_signal_emit(self._signals.finished.emit, self._root, False)

    def cancel(self) -> None:
        """Request cancellation of the current load operation."""

        self._is_cancelled = True

    # ------------------------------------------------------------------
    def _build_payload_chunks(self) -> Iterable[List[Dict[str, object]]]:
        ensure_work_dir(self._root)

        # Determine the effective index root and album path using helper
        effective_index_root, album_path = compute_album_path(
            self._root,
            self._library_root,
        )
        root_name = self._root.name
        library_root_name = self._library_root.name if self._library_root else None
        index_root_name = effective_index_root.name
        LOGGER.debug(
            "AssetLoaderWorker._build_payload_chunks root=%s library_root=%s index_root=%s "
            "album_path=%s filter_params=%s",
            root_name,
            library_root_name,
            index_root_name,
            album_path,
            self._filter_params,
        )

        query_service = require_query_service(
            effective_index_root,
            self._asset_query_service,
        )
        location_writer = query_service.location_cache_writer(self._root)

        # Emit indeterminate progress initially
        _safe_signal_emit(self._signals.progressUpdated.emit, self._root, 0, 0)

        # Prepare filter params with featured list if needed
        params = copy.deepcopy(self._filter_params) if self._filter_params else {}
        if album_path is None and self._library_root is not None:
            params.setdefault("exclude_path_prefix", RECENTLY_DELETED_DIR_NAME)

        # 2. Stream rows using the session-backed lightweight geometry query.
        dir_cache: Dict[Path, Optional[Set[str]]] = {}

        def _path_exists(path: Path) -> bool:
            return _cached_path_exists(path, dir_cache)

        generator = query_service.read_geometry_rows(
            self._root,
            filter_params=params,
            sort_by_date=True,
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

            entry = build_asset_entry(
                self._root,
                row,
                self._featured,
                location_writer,
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
                        total = query_service.count_assets(
                            self._root,
                            filter_hidden=True,
                            filter_params=params,
                        )
                        total_calculated = True
                    except Exception as exc:
                        LOGGER.warning(
                            "Failed to count assets in database: %s",
                            exc,
                            exc_info=True,
                        )
                        total = 0  # fallback

            # Update progress periodically
            # Use >= total to robustly handle concurrent additions where position might exceed original total
            if total_calculated and (
                position >= total or position - last_reported >= 50
            ):
                last_reported = position
                _safe_signal_emit(
                    self._signals.progressUpdated.emit,
                    self._root,
                    position,
                    total,
                )

        if chunk:
            yielded_count += len(chunk)
            yield chunk

        # Final progress update
        if not total_calculated:  # If we never flushed (e.g. small album)
            total = yielded_count
        _safe_signal_emit(self._signals.progressUpdated.emit, self._root, total, total)


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
        self._items = self._apply_live_pairing(items)
        self._featured = normalize_featured(featured)
        self._signals = signals
        self._filter_params = filter_params or {}
        self._is_cancelled = False
        self._dir_cache: Dict[Path, Optional[Set[str]]] = {}

    def _path_exists(self, path: Path) -> bool:
        return _cached_path_exists(path, self._dir_cache)

    @staticmethod
    def _normalize_live_role(value: object) -> Optional[int]:
        """Return a normalized live role integer when possible."""
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    def _apply_live_pairing(
        self, items: List[Dict[str, object]]
    ) -> List[Dict[str, object]]:
        """Enrich scan items with Live Photo pairing metadata."""
        groups = pair_live(items)
        if not groups:
            return list(items)
        partner_map: Dict[str, str] = {}
        role_map: Dict[str, int] = {}
        for group in groups:
            if not group.still or not group.motion:
                continue
            partner_map[group.still] = group.motion
            partner_map[group.motion] = group.still
            role_map[group.still] = 0
            role_map[group.motion] = 1
        if not partner_map:
            return list(items)
        enriched: List[Dict[str, object]] = []
        for item in items:
            rel = item.get("rel")
            updated = dict(item)
            raw_live_role = updated.get("live_role")
            live_role = self._normalize_live_role(raw_live_role)
            if live_role is not None and isinstance(raw_live_role, str):
                updated["live_role"] = live_role
            if isinstance(rel, str) and rel in partner_map:
                updated["live_partner_rel"] = partner_map[rel]
                paired_live_role = role_map[rel]
                if live_role is None or live_role == paired_live_role:
                    updated["live_role"] = paired_live_role
            enriched.append(updated)
        return enriched

    def _should_include_row(self, row: Dict[str, object]) -> bool:
        """Check if a row should be included based on filter_params.

        This applies the same filter semantics as AssetRepository._build_filter_clauses
        but operates on in-memory row dictionaries instead of SQL.

        Key differences from the database filter:
        - For 'favorites': Also checks the featured set, since live scan items
          may not yet have is_favorite persisted in the database.
        """
        filter_mode = self._filter_params.get("filter_mode")
        prefix = self._filter_params.get("exclude_path_prefix")
        prefix_norm = Path(prefix).as_posix() if isinstance(prefix, str) and prefix.strip() else None
        if prefix_norm:
            # Live ingest rows bypass the database-level filter, so guard here as well.
            rel_value = row.get("rel")
            if isinstance(rel_value, str):
                rel_norm = Path(rel_value).as_posix()
                if rel_norm == prefix_norm or rel_norm.startswith(prefix_norm + "/"):
                    return False
        live_role = self._normalize_live_role(row.get("live_role"))
        if live_role == 1:
            return False

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
                    if not _safe_signal_emit(self._signals.chunkReady.emit, self._root, list(chunk)):
                        return  # Signal source deleted, stop processing
                    chunk = []

            if chunk and not self._is_cancelled:
                if not _safe_signal_emit(self._signals.chunkReady.emit, self._root, chunk):
                    return  # Signal source deleted, stop processing

            if not self._is_cancelled:
                _safe_signal_emit(self._signals.finished.emit, self._root, True)
            else:
                _safe_signal_emit(self._signals.finished.emit, self._root, False)

        except Exception as exc:
            LOGGER.error("Error processing live items: %s", exc, exc_info=True)
            if not self._is_cancelled:
                _safe_signal_emit(self._signals.error.emit, self._root, str(exc))
            _safe_signal_emit(self._signals.finished.emit, self._root, False)
