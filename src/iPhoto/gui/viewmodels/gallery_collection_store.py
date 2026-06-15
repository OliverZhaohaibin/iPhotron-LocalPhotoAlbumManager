"""Pure Python collection store backing gallery and filmstrip selection."""

from __future__ import annotations

import copy
import os
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Protocol

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models.query import AssetQuery, WindowResult
from iPhoto.gui.gallery_demand import (
    MICRO_QUERY_CHUNK,
    MICRO_WARM_LIMIT,
    GalleryViewportDemand,
)
from iPhoto.gui.viewmodels.asset_dto_converter import (
    geotagged_asset_to_dto as _geotagged_asset_to_dto_fn,
)
from iPhoto.gui.viewmodels.asset_dto_converter import (
    is_legacy_thumb_path as _is_legacy_thumb_path_fn,
)
from iPhoto.gui.viewmodels.asset_dto_converter import (
    resolve_abs_path as _resolve_abs_path_fn,
)
from iPhoto.gui.viewmodels.asset_dto_converter import (
    scan_row_is_thumbnail as _scan_row_is_thumbnail_fn,
)
from iPhoto.gui.viewmodels.asset_dto_converter import (
    scan_row_to_dto as _scan_row_to_dto_fn,
)
from iPhoto.gui.viewmodels.asset_paging import (
    should_validate_paths as _should_validate_paths_fn,
)
from iPhoto.gui.viewmodels.gallery_window_loader import GalleryWindowRequest, GalleryWindowResult
from iPhoto.gui.viewmodels.path_cache import PathExistsCache
from iPhoto.gui.viewmodels.pending_move_buffer import (
    _PendingMove,
)
from iPhoto.gui.viewmodels.pending_move_buffer import (
    pending_source_matches_query as _pending_source_matches_query_fn,
)
from iPhoto.gui.viewmodels.pending_move_buffer import (
    should_include_pending as _should_include_pending_fn,
)
from iPhoto.gui.viewmodels.signal import Signal
from iPhoto.infrastructure.services.performance_events import emit_perf_event


class GalleryAssetQuerySurface(Protocol):
    """Session-owned query surface used by the gallery collection store."""

    library_root: Path

    def count_query_assets(self, query: AssetQuery) -> int:
        """Return the number of assets matching *query*."""

    def read_query_asset_rows(
        self,
        root: Path,
        query: AssetQuery,
    ) -> Iterable[dict]:
        """Yield rows matching *query*, scoped to *root*."""

    def read_query_asset_window(
        self,
        root: Path,
        query: AssetQuery,
        first: int,
        limit: int,
    ) -> WindowResult:
        """Return a bounded scoped window for *query*."""

    def read_thumbnail_hint_window(
        self,
        root: Path,
        query: AssetQuery,
        first: int,
        limit: int,
    ) -> WindowResult:
        """Return paths and existing full-thumbnail cache keys."""

    def find_row_by_path(self, query: AssetQuery, path: Path) -> Optional[int]:
        """Return a row index for *path* inside *query*."""

    def find_live_partner(self, asset_id: str) -> Optional[dict]:
        """Return a live partner row for *asset_id*."""


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _ranges_intersect(first_a: int, last_a: int, first_b: int, last_b: int) -> bool:
    return first_a <= last_b and first_b <= last_a


class GalleryCollectionStore:
    """Pure Python gallery data store with viewport-aware caching."""

    INITIAL_VISIBLE_ROWS = 80
    MIN_WINDOW_SIZE = 300
    MAX_WINDOW_SIZE = MICRO_WARM_LIMIT
    WINDOW_MULTIPLIER = 4
    LOOKBEHIND_SCREENS = 1
    LOOKAHEAD_SCREENS = 2
    HYSTERESIS_RATIO = 0.25

    def __init__(
        self,
        asset_query_service: GalleryAssetQuerySurface | None,
        library_root: Optional[Path] = None,
    ) -> None:
        self.data_changed = Signal()
        self.window_changed = Signal()
        self.count_changed = Signal()
        self.row_changed = Signal()
        self.row_loaded = Signal()
        self.thumbnail_backfill_scheduled = Signal()

        self._asset_query_service = asset_query_service
        self._library_root = library_root or getattr(asset_query_service, "library_root", None)
        self._current_query: Optional[AssetQuery] = None
        self._selection_query: Optional[AssetQuery] = None
        self._selection_direct_assets: Optional[list] = None
        self._selection_library_root: Optional[Path] = library_root
        self._total_count = 0
        self._row_cache: OrderedDict[int, AssetDTO] = OrderedDict()
        self._window_range: Optional[tuple[int, int]] = None
        self._visible_range: Optional[tuple[int, int]] = None
        self._warm_range: Optional[tuple[int, int]] = None
        self._active_root: Optional[Path] = None
        self._path_cache = PathExistsCache()
        self._pending_moves: List[_PendingMove] = []
        self._pending_paths: set[str] = set()
        self._pinned_row: Optional[int] = None
        self._pending_scan_refresh = False
        self._pending_scan_rels: set[str] = set()
        self._pending_scan_sort_keys: set[tuple[str, str]] = set()
        self._thumbnail_backfill_windows: set[tuple[int, int]] = set()
        self._thumbnail_backfill_pending = False
        self._direct_mode = False
        self._collection_revision = 0
        self._request_generation = 0
        self._demand_generation = 0
        self._pending_window_generations: set[int] = set()
        self._window_request_handler: Callable[[GalleryWindowRequest], None] | None = None
        self._pending_row_loads: set[int] = set()

    def set_library_root(self, root: Optional[Path]) -> None:
        if self._library_root == root:
            return
        self._library_root = root
        self._reset_window_state(clear_pending=True)
        self.data_changed.emit()

    def set_asset_query_service(
        self,
        asset_query_service: GalleryAssetQuerySurface | None,
    ) -> None:
        if self._asset_query_service is asset_query_service:
            return
        self._asset_query_service = asset_query_service
        self._reset_window_state()
        self.data_changed.emit()

    def set_window_request_handler(
        self,
        handler: Callable[[GalleryWindowRequest], None] | None,
    ) -> None:
        """Use *handler* for non-blocking Gallery window requests."""

        self._window_request_handler = handler

    def rebind_asset_query_service(
        self,
        asset_query_service: GalleryAssetQuerySurface | None,
        library_root: Optional[Path],
    ) -> None:
        self.set_asset_query_service(asset_query_service)
        self.set_library_root(library_root)

    @property
    def asset_query_service(self) -> GalleryAssetQuerySurface | None:
        return self._asset_query_service

    def set_active_root(self, root: Optional[Path]) -> None:
        self._active_root = root

    def active_root(self) -> Optional[Path]:
        return self._active_root

    def library_root(self) -> Optional[Path]:
        return self._library_root

    def current_query(self) -> Optional[AssetQuery]:
        return self._clone_query(self._selection_query) if self._selection_query is not None else None

    def current_direct_assets(self) -> Optional[list]:
        return list(self._selection_direct_assets) if self._selection_direct_assets is not None else None

    def load_selection(
        self,
        active_root: Optional[Path],
        *,
        query: Optional[AssetQuery] = None,
        direct_assets: Optional[list] = None,
        library_root: Optional[Path] = None,
    ) -> None:
        has_query = query is not None
        has_direct_assets = direct_assets is not None
        if has_query == has_direct_assets:
            raise ValueError("Exactly one of query or direct_assets must be provided.")

        self.set_active_root(active_root)
        if query is not None:
            self._load_query(query)
            return

        resolved_library_root = library_root or self._library_root or active_root
        if resolved_library_root is None:
            raise ValueError("library_root is required when loading direct assets.")
        self._load_direct_assets(list(direct_assets or []), resolved_library_root)

    def reload_current_selection(self) -> None:
        if self._selection_direct_assets is not None:
            self._load_direct_assets(
                list(self._selection_direct_assets),
                self._selection_library_root or self._library_root,
            )
            return
        if self._selection_query is not None:
            self._load_query(self._selection_query)

    def _load_query(self, query: AssetQuery) -> None:
        old_total = self._total_count
        self._selection_query = self._clone_query(query)
        self._selection_direct_assets = None
        self._selection_library_root = self._library_root
        self._current_query = self._clone_query(query)
        self._direct_mode = False
        self._reset_window_state()
        if self._window_request_handler is None:
            self._load_initial_window()
        else:
            self._visible_range = (0, max(0, self.INITIAL_VISIBLE_ROWS - 1))
            self._request_async_window(*self._visible_range)
        self._emit_refresh(old_total)

    def _load_direct_assets(self, assets: list, library_root: Path) -> None:
        old_total = self._total_count
        stored_assets = list(assets)
        self._selection_query = None
        self._selection_direct_assets = stored_assets
        self._selection_library_root = library_root
        self._current_query = None
        self._library_root = library_root
        self._direct_mode = True
        self._reset_window_state()

        next_index = 0
        for asset in stored_assets:
            dto = self._geotagged_asset_to_dto(asset, library_root)
            if dto is not None and not self._dto_matches_pending_source(dto):
                self._row_cache[next_index] = dto
                next_index += 1

        self._total_count = len(self._row_cache)
        if self._total_count > 0:
            self._window_range = (0, self._total_count - 1)
            self._visible_range = self._window_range

        self._emit_refresh(old_total)

    def _emit_refresh(self, old_total: int) -> None:
        if old_total != self._total_count:
            self.count_changed.emit(old_total, self._total_count)
        self.data_changed.emit()
        if self._window_range is not None:
            self.window_changed.emit(*self._window_range)

    def asset_at(self, index: int) -> Optional[AssetDTO]:
        dto = self._row_cache.get(index)
        if dto is not None:
            self._row_cache.move_to_end(index)
        if dto is not None or self._direct_mode:
            return dto
        if index < 0 or index >= self._total_count:
            return None
        return None

    def ensure_row_loaded(self, row: int, *, emit_signals: bool = True) -> bool:
        """Load the bounded gallery window containing *row* if needed."""

        if row < 0:
            return False
        if row in self._row_cache:
            return True
        if self._direct_mode:
            return self.asset_at(row) is not None
        if self._current_query is None:
            return False
        if self._window_request_handler is not None:
            self._pending_row_loads.add(row)
            self._request_async_window(row, row, retain_when_stale=True)
            return False
        if self._total_count == 0 and self._window_range is None:
            self._load_initial_window()
        if row >= self._total_count:
            return False

        self._reload_window_for_visible_range(row, row, emit_signals=emit_signals)
        return row in self._row_cache

    def snapshot_signature(self) -> tuple[int, Optional[tuple[int, int]], int]:
        return (self._total_count, self._window_range, self._collection_revision)

    def live_partner_for(self, asset_id: str, root: Optional[Path] = None) -> Optional[AssetDTO]:
        find_live_partner = getattr(self._asset_query_service, "find_live_partner", None)
        if not callable(find_live_partner):
            return None
        row = find_live_partner(asset_id)
        if not isinstance(row, dict):
            return None
        active_root = root or self._library_root
        if active_root is None:
            return None
        rel = row.get("rel")
        if not isinstance(rel, str) or not rel:
            return None
        return self._scan_row_to_dto(active_root, rel, row)

    def find_dto_by_path(self, path: Path) -> Optional[AssetDTO]:
        target = self._normalize_abs_key(path)
        for dto in self._row_cache.values():
            if self._normalize_abs_key(dto.abs_path) == target:
                return dto
        if self._selection_direct_assets is not None:
            for asset in self._selection_direct_assets:
                dto = self._geotagged_asset_to_dto(
                    asset,
                    self._selection_library_root or self._library_root or path.parent,
                )
                if (
                    dto is not None
                    and not self._dto_matches_pending_source(dto)
                    and self._normalize_abs_key(dto.abs_path) == target
                ):
                    return dto
        return None

    def row_for_path(self, path: Path) -> Optional[int]:
        target = self._normalize_abs_key(path)
        if target in self._pending_paths:
            return None
        for row, dto in self._row_cache.items():
            if self._normalize_abs_key(dto.abs_path) == target:
                return row

        if self._selection_direct_assets is not None:
            library_root = self._selection_library_root or self._library_root
            if library_root is None:
                return None
            next_index = 0
            for asset in self._selection_direct_assets:
                dto = self._geotagged_asset_to_dto(asset, library_root)
                if dto is None or self._dto_matches_pending_source(dto):
                    continue
                if self._normalize_abs_key(dto.abs_path) == target:
                    return next_index
                next_index += 1
            return None

        if self._current_query is None or self._total_count <= 0:
            return None

        find_row_by_path = getattr(self._asset_query_service, "find_row_by_path", None)
        if not callable(find_row_by_path):
            return None
        raw_row = find_row_by_path(self._current_query, path)
        if raw_row is None:
            return None
        return self._pending_adjusted_view_row_for_raw_row(
            self._current_query,
            int(raw_row),
        )

    def count(self) -> int:
        return self._total_count

    def update_favorite_status(self, row: int, is_favorite: bool) -> None:
        dto = self._row_cache.get(row)
        if dto is None and row >= 0:
            dto = self.asset_at(row)
        if dto is None:
            return
        dto.is_favorite = is_favorite
        self.row_changed.emit(row)

    def update_asset_metadata(self, row: int, metadata: Dict[str, object]) -> None:
        dto = self._row_cache.get(row)
        if dto is None and row >= 0:
            dto = self.asset_at(row)
        if dto is None:
            return
        dto.metadata = dict(metadata)
        width = metadata.get("w")
        height = metadata.get("h")
        duration = metadata.get("dur")
        size_bytes = metadata.get("bytes")
        if isinstance(width, int) and width > 0:
            dto.width = width
        if isinstance(height, int) and height > 0:
            dto.height = height
        if isinstance(duration, (int, float)) and float(duration) >= 0.0:
            dto.duration = float(duration)
        if isinstance(size_bytes, int) and size_bytes >= 0:
            dto.size_bytes = size_bytes
        self.row_changed.emit(row)

    def remove_rows(self, rows: List[int], *, emit: bool = True) -> None:
        if not rows:
            return
        removed = sorted({row for row in rows if 0 <= row < self._total_count})
        if not removed:
            return

        old_total = self._total_count
        removed_set = set(removed)
        new_cache: Dict[int, AssetDTO] = {}
        removed_before = 0
        removed_index = 0
        removed_count = len(removed)
        for row in sorted(self._row_cache):
            while removed_index < removed_count and removed[removed_index] < row:
                removed_before += 1
                removed_index += 1
            if row in removed_set:
                continue
            new_cache[row - removed_before] = self._row_cache[row]

        self._row_cache = OrderedDict(sorted(new_cache.items()))
        self._total_count = max(0, old_total - len(removed))
        self._window_range = None
        self._visible_range = None if self._total_count == 0 else self._visible_range

        if self._pinned_row is not None:
            if self._pinned_row in removed_set:
                self._pinned_row = None
            else:
                shift = sum(1 for row in removed if row < self._pinned_row)
                self._pinned_row -= shift

        if emit:
            self.count_changed.emit(old_total, self._total_count)
            if self._total_count > 0:
                self.window_changed.emit(max(0, removed[0] - 1), self._total_count - 1)
            self.data_changed.emit()

    def apply_optimistic_move(
        self,
        paths: List[Path],
        destination_root: Path,
        *,
        is_delete: bool,
    ) -> tuple[list[int], list[AssetDTO]]:
        if not paths:
            return [], []
        destination_album_path = self._album_path_for_root(destination_root)
        removed_rows: list[int] = []
        inserted_dtos: list[AssetDTO] = []
        cached_map = {
            self._normalize_abs_key(dto.abs_path): (idx, dto)
            for idx, dto in self._iter_cached_rows()
        }
        for path in paths:
            key = self._normalize_abs_key(path)
            if key in self._pending_paths:
                continue
            found = cached_map.get(key)
            if found is None:
                continue
            row, dto = found
            removed_rows.append(row)
            destination_abs = destination_root / path.name
            source_library_rel = self._rel_path_for_abs(path)
            destination_rel = self._rel_path_for_abs(destination_abs)
            moved_dto = AssetDTO(
                id=dto.id,
                abs_path=destination_abs,
                rel_path=destination_rel,
                media_type=dto.media_type,
                created_at=dto.created_at,
                width=dto.width,
                height=dto.height,
                duration=dto.duration,
                size_bytes=dto.size_bytes,
                metadata=dto.metadata,
                is_favorite=dto.is_favorite,
                is_live=dto.is_live,
                is_pano=dto.is_pano,
                micro_thumbnail=dto.micro_thumbnail,
            )
            pending = _PendingMove(
                dto=moved_dto,
                source_id=str(dto.id) if dto.id else None,
                source_abs=path,
                source_library_rel=source_library_rel,
                source_album_path=self._album_path_for_rel(source_library_rel),
                destination_root=destination_root,
                destination_album_path=destination_album_path,
                destination_abs=destination_abs,
                destination_rel=destination_rel,
                is_delete=is_delete,
            )
            self._pending_moves.append(pending)
            self._pending_paths.add(key)
            if self._current_query and self._should_include_pending(pending, self._current_query):
                inserted_dtos.append(moved_dto)
        return removed_rows, inserted_dtos

    def clear_pending_moves_for_paths(self, paths: Iterable[Path]) -> bool:
        """Clear pending mutations whose source or destination matches *paths*."""

        keys = {self._normalize_abs_key(Path(path)) for path in paths}
        if not keys:
            return False
        before = len(self._pending_moves)
        self._pending_moves = [
            pending
            for pending in self._pending_moves
            if self._normalize_abs_key(pending.source_abs) not in keys
            and self._normalize_abs_key(pending.destination_abs) not in keys
        ]
        self._pending_paths = {
            self._normalize_abs_key(pending.source_abs)
            for pending in self._pending_moves
        }
        return len(self._pending_moves) != before

    def clear_all_pending_moves(self) -> bool:
        """Drop every optimistic mutation overlay for this collection store."""

        had_pending = bool(self._pending_moves or self._pending_paths)
        self._pending_moves.clear()
        self._pending_paths.clear()
        return had_pending

    def append_dtos(self, dtos: List[AssetDTO]) -> None:
        if not dtos:
            return
        start = self._total_count
        for offset, dto in enumerate(dtos):
            self._row_cache[start + offset] = dto
        old_total = self._total_count
        self._total_count += len(dtos)
        self.count_changed.emit(old_total, self._total_count)
        self.data_changed.emit()

    def prioritize_rows(self, first: int, last: int) -> None:
        if self._direct_mode or self._current_query is None:
            return
        if self._total_count == 0 and self._window_range is None:
            if self._window_request_handler is not None:
                self._visible_range = (max(0, first), max(first, last))
                self._request_async_window(*self._visible_range)
                return
            self._load_initial_window()
            if self._total_count == 0:
                return

        first = max(0, first)
        last = max(first, last)
        if self._total_count > 0:
            last = min(last, self._total_count - 1)
        self._visible_range = (first, last)

        if self._window_request_handler is not None:
            if self._window_needs_reload(first, last):
                self._request_async_window(first, last)
            return

        should_refresh = self._pending_scan_refresh and (
            first == 0 or self._window_rel_intersects_pending_scan(first, last)
        )
        if should_refresh or self._window_needs_reload(first, last):
            self._reload_window_for_visible_range(
                first,
                last,
                emit_signals=True,
                emit_source_changed=should_refresh,
            )

    def reconcile_viewport_demand(self, demand: GalleryViewportDemand) -> None:
        """Warm visible, hot, and micro ranges without replacing cached rows."""

        self._visible_range = demand.visible_range
        self._warm_range = demand.warm_range
        self._window_range = demand.warm_range
        self._demand_generation = max(self._demand_generation, int(demand.generation))
        if self._direct_mode or self._current_query is None:
            return
        if self._window_request_handler is None:
            self.prioritize_rows(*demand.warm_range)
            return

        chunks = []
        for chunk_first in range(demand.warm_first, demand.warm_last + 1, MICRO_QUERY_CHUNK):
            chunk_last = min(demand.warm_last, chunk_first + MICRO_QUERY_CHUNK - 1)
            if all(row in self._row_cache for row in range(chunk_first, chunk_last + 1)):
                continue
            if _ranges_intersect(
                chunk_first,
                chunk_last,
                demand.visible_first,
                demand.visible_last,
            ):
                priority = 0
            elif _ranges_intersect(
                chunk_first,
                chunk_last,
                demand.full_prefetch_first,
                demand.full_prefetch_last,
            ):
                priority = 1
            else:
                priority = 2
            direction_order = chunk_first if demand.direction >= 0 else -chunk_last
            chunks.append((priority, direction_order, chunk_first, chunk_last))

        for priority, _direction_order, chunk_first, chunk_last in sorted(chunks):
            self._request_async_chunk(
                chunk_first,
                chunk_last,
                demand_generation=demand.generation,
                priority=priority,
            )
        self._trim_row_cache()
        emit_perf_event(
            "gallery_viewport_demand",
            generation=demand.generation,
            phase=demand.phase,
            screens_per_second=round(demand.screens_per_second, 3),
            visible_count=demand.visible_last - demand.visible_first + 1,
            full_prefetch_count=(
                demand.full_prefetch_last - demand.full_prefetch_first + 1
            ),
            warm_count=demand.warm_last - demand.warm_first + 1,
            cached_count=len(self._row_cache),
            queued_chunks=len(chunks),
        )

    def apply_window_result(self, result: GalleryWindowResult) -> bool:
        """Publish one background-loaded window on the owning GUI thread."""

        was_pending = result.generation in self._pending_window_generations
        self._pending_window_generations.discard(result.generation)
        if (
            not was_pending
            or result.error is not None
            or (
                result.requested_revision > 0
                and result.requested_revision != self._collection_revision
                and result.collection_revision < self._collection_revision
            )
        ):
            return False
        if (
            self._demand_generation > 0
            and result.demand_generation < self._demand_generation
        ):
            relevant_rows = {
                row: dto
                for row, dto in result.rows.items()
                if self._row_is_currently_relevant(row)
            }
            if not relevant_rows:
                return False
        else:
            relevant_rows = result.rows
        old_total = self._total_count
        if (
            self._collection_revision > 0
            and result.collection_revision > self._collection_revision
        ):
            self._row_cache.clear()
        for row, dto in relevant_rows.items():
            self._row_cache[row] = dto
            self._row_cache.move_to_end(row)
        self._total_count = max(0, int(result.total_count))
        self._trim_row_cache()
        self._window_range = self._cache_window_range()
        loaded_rows = sorted(self._pending_row_loads.intersection(self._row_cache))
        completed_pending_rows = {
            row
            for row in self._pending_row_loads
            if row in self._row_cache
            or row >= self._total_count
            or result.first <= row <= result.last
        }
        self._pending_row_loads.difference_update(completed_pending_rows)
        self._collection_revision = max(self._collection_revision, result.collection_revision)
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()
        if result.backfill_queued > 0:
            self._thumbnail_backfill_pending = True

        if old_total != self._total_count:
            self.count_changed.emit(old_total, self._total_count)
            self.data_changed.emit()
        elif relevant_rows:
            self.window_changed.emit(min(relevant_rows), max(relevant_rows))
        for row in loaded_rows:
            self.row_loaded.emit(row)
        return True

    def discard_window_requests(self, generations: Iterable[int]) -> None:
        """Forget queued requests canceled before their worker started."""

        self._pending_window_generations.difference_update(int(item) for item in generations)

    def cached_rows(self, first: int, last: int) -> list[tuple[int, AssetDTO]]:
        """Return cached rows only; never load or query."""

        return [
            (row, self._row_cache[row])
            for row in range(max(0, first), max(first, last) + 1)
            if row in self._row_cache
        ]

    def cached_row_for_path(self, path: Path) -> int | None:
        target = self._normalize_abs_key(path)
        for row, dto in self._row_cache.items():
            if self._normalize_abs_key(dto.abs_path) == target:
                return row
        return None

    def _request_async_window(
        self,
        first: int,
        last: int,
        *,
        retain_when_stale: bool = False,
    ) -> None:
        if (
            self._window_request_handler is None
            or self._current_query is None
            or self._asset_query_service is None
        ):
            return
        root = self._active_root or self._library_root
        if root is None:
            return
        view_first, limit = self._compute_target_window_unbounded(first, last)
        self._request_async_chunk(
            view_first,
            view_first + limit - 1,
            demand_generation=0,
            priority=0,
            retain_when_stale=retain_when_stale,
        )

    def _request_async_chunk(
        self,
        first: int,
        last: int,
        *,
        demand_generation: int,
        priority: int,
        retain_when_stale: bool = False,
    ) -> None:
        if (
            self._window_request_handler is None
            or self._current_query is None
            or self._asset_query_service is None
        ):
            return
        root = self._active_root or self._library_root
        if root is None:
            return
        view_first = max(0, int(first))
        limit = max(0, int(last) - view_first + 1)
        if limit <= 0:
            return
        self._request_generation += 1
        generation = self._request_generation
        self._pending_window_generations.add(generation)
        raw_first = (
            self._pending_adjusted_raw_offset_for_view_offset(self._current_query, view_first)
            if self._pending_moves
            else view_first
        )
        pending_sources = tuple(
            pending
            for pending in self._pending_moves
            if self._pending_source_matches_query(pending, self._current_query)
        )
        pending_insertions = tuple(
            self._pending_insertions_for_query(self._current_query, ())
        )
        self._window_request_handler(
            GalleryWindowRequest(
                generation=generation,
                root=Path(root),
                query=self._clone_query(self._current_query),
                query_service=self._asset_query_service,
                view_first=view_first,
                raw_first=raw_first,
                limit=limit,
                pending_source_ids=frozenset(
                    str(pending.source_id)
                    for pending in pending_sources
                    if pending.source_id is not None
                ),
                pending_source_count=len(pending_sources),
                pending_insertions=pending_insertions,
                collection_revision=self._collection_revision,
                demand_generation=int(demand_generation),
                priority=int(priority),
                retain_when_stale=retain_when_stale,
            )
        )

    def _row_is_currently_relevant(self, row: int) -> bool:
        if row == self._pinned_row:
            return True
        if row in self._pending_row_loads:
            return True
        if self._warm_range is None:
            return False
        return self._warm_range[0] <= row <= self._warm_range[1]

    def _trim_row_cache(self) -> None:
        max_items = MICRO_WARM_LIMIT + (1 if self._pinned_row is not None else 0)
        while len(self._row_cache) > max_items:
            evict = next(
                (
                    row
                    for row in self._row_cache
                    if row != self._pinned_row and not self._row_is_currently_relevant(row)
                ),
                None,
            )
            if evict is None:
                evict = next(
                    (row for row in self._row_cache if row != self._pinned_row),
                    None,
                )
            if evict is None:
                break
            self._row_cache.pop(evict, None)

    def _cache_window_range(self) -> tuple[int, int] | None:
        if not self._row_cache or self._total_count <= 0:
            return None
        if self._warm_range is not None:
            return self._warm_range
        rows = self._row_cache.keys()
        return min(rows), max(rows)

    def pin_row(self, row: int) -> None:
        if row < 0 or row >= self._total_count:
            self._pinned_row = None
            return
        self._pinned_row = row
        self.ensure_row_loaded(row, emit_signals=True)

    def _record_scan_rows(self, scan_root: Path, rows: List[dict]) -> bool:
        """Record ready batch rows and defer the visible-window refresh."""

        if not rows or self._current_query is None or self._active_root is None:
            return False
        mapped_entries = self._map_scan_rows_to_active_entries(scan_root, rows)
        if not mapped_entries:
            return False

        self._pending_scan_rels.update(view_rel for view_rel, _row in mapped_entries)
        self._pending_scan_sort_keys.update(
            sort_key
            for _view_rel, row in mapped_entries
            for sort_key in [self._sort_key_from_scan_row(row)]
            if sort_key is not None
        )
        self._pending_scan_refresh = True
        return True

    def handle_scan_batch(self, batch: object) -> None:
        """Consume an explicit ready-only scan batch without depending on legacy chunks."""

        if self.record_scan_batch(batch):
            self.flush_pending_scan_refresh()

    def record_scan_batch(self, batch: object) -> bool:
        """Record a ready-only scan batch and defer any GUI refresh."""

        rows = getattr(batch, "rows", None)
        root = getattr(batch, "root", None)
        job_id = str(getattr(batch, "job_id", "") or "")
        is_thumbnail_backfill = job_id.startswith("thumbnail-backfill:")
        revision = getattr(batch, "collection_revision", None)
        if isinstance(revision, int):
            self._collection_revision = max(self._collection_revision, revision)
        if is_thumbnail_backfill:
            self._thumbnail_backfill_windows.clear()
            self._thumbnail_backfill_pending = False
        if root is None:
            return False
        if not rows:
            if (
                is_thumbnail_backfill
                and not self._pending_scan_rels
                and not self._pending_scan_sort_keys
            ):
                self._pending_scan_refresh = False
            return False
        return self._record_scan_rows(Path(root), list(rows))

    def handle_scan_finished(self, root: Path, success: bool) -> None:
        if not success or self._current_query is None or self._active_root is None:
            return
        if not self._scan_root_matches_active_root(root):
            return
        self._pending_scan_refresh = True
        if self._visible_range is not None:
            first, last = self._visible_range
            if self._window_request_handler is not None:
                self._request_async_window(first, last)
                return
            self._reload_window_for_visible_range(
                first,
                last,
                emit_signals=True,
                emit_source_changed=True,
            )

    def flush_pending_scan_refresh(self) -> None:
        """Refresh the visible window after recorded scan/backfill rows."""

        if not self._pending_scan_refresh or self._current_query is None:
            self._pending_scan_refresh = False
            self._pending_scan_rels.clear()
            self._pending_scan_sort_keys.clear()
            return
        if self._visible_range is None:
            first = 0
            last = max(0, self.INITIAL_VISIBLE_ROWS - 1)
        else:
            first, last = self._visible_range
            if first != 0 and not self._pending_scan_affects_visible_window(first, last):
                return
        if self._window_request_handler is not None:
            self._request_async_window(first, last)
            return
        self._reload_window_for_visible_range(
            first,
            last,
            emit_signals=True,
            emit_source_changed=True,
        )

    def _load_initial_window(self) -> None:
        visible_last = max(0, self.INITIAL_VISIBLE_ROWS - 1)
        self._visible_range = (0, visible_last)
        self._reload_window_for_visible_range(0, visible_last, emit_signals=False)

    def _reload_window_for_visible_range(
        self,
        first: int,
        last: int,
        *,
        emit_signals: bool,
        emit_source_changed: bool = False,
    ) -> None:
        if self._current_query is None:
            return
        request_generation = self._request_generation + 1
        self._request_generation = request_generation

        fetch_result = self._fetch_window_for_visible_range(first, last)
        new_total = fetch_result[0]
        if new_total <= 0:
            queued_backfill = self._request_visible_thumbnail_backfill(first, last)
            if new_total <= 0:
                old_total = self._total_count
                if queued_backfill > 0 or self._thumbnail_backfill_pending:
                    self._row_cache.clear()
                    self._total_count = 0
                    self._window_range = None
                    self._visible_range = (first, last)
                    self._pinned_row = None
                else:
                    self._reset_window_state()
                if emit_signals and old_total != 0:
                    self.count_changed.emit(old_total, 0)
                    self.data_changed.emit()
                return

        first = max(0, min(first, new_total - 1))
        last = max(first, min(last, new_total - 1))
        _new_total, window_first, window_last, fetched_rows, collection_revision = fetch_result
        if window_last < window_first:
            window_first, window_last = self._compute_target_window(first, last, new_total)
        if request_generation != self._request_generation:
            return

        old_total = self._total_count
        previous_cache = self._row_cache
        new_cache = dict(fetched_rows)
        pending_insertions = (
            self._pending_insertions_for_query(self._current_query, new_cache.values())
            if self._current_query is not None
            else []
        )
        for offset, dto in enumerate(pending_insertions):
            new_cache[new_total - len(pending_insertions) + offset] = dto

        pinned_row = self._pinned_row
        if pinned_row is not None and pinned_row not in new_cache and 0 <= pinned_row < new_total:
            pinned_dto = previous_cache.get(pinned_row)
            if pinned_dto is None:
                pinned_dto = self._fetch_single_row(pinned_row)
            if pinned_dto is not None:
                new_cache[pinned_row] = pinned_dto

        self._row_cache = OrderedDict(sorted(new_cache.items()))
        self._total_count = new_total
        self._window_range = (window_first, window_last)
        self._visible_range = (first, last)
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()
        self._collection_revision = max(self._collection_revision + 1, collection_revision)

        if emit_signals:
            if old_total != new_total:
                self.count_changed.emit(old_total, new_total)
                emit_source_changed = True
            if emit_source_changed:
                self.data_changed.emit()
            self.window_changed.emit(window_first, window_last)
            if pinned_row is not None and pinned_row not in fetched_rows and pinned_row in self._row_cache:
                self.window_changed.emit(pinned_row, pinned_row)

        if self._request_visible_thumbnail_backfill(first, last) > 0:
            self._pending_scan_refresh = True

    def flush_pending_thumbnail_backfill(self) -> bool:
        """Reload the visible window after queued stale thumbnail backfill work."""

        if not self._thumbnail_backfill_pending:
            return False
        if self._visible_range is None or self._current_query is None:
            self._thumbnail_backfill_pending = False
            return False
        first, last = self._visible_range
        if self._window_request_handler is not None:
            self._request_async_window(first, last)
            return True
        self._reload_window_for_visible_range(
            first,
            last,
            emit_signals=True,
            emit_source_changed=True,
        )
        pending_check = getattr(self._asset_query_service, "thumbnail_backfill_pending", None)
        still_pending = bool(pending_check()) if callable(pending_check) else False
        if not still_pending:
            self._thumbnail_backfill_pending = False
        return still_pending

    def _fetch_window_for_visible_range(
        self,
        first: int,
        last: int,
    ) -> tuple[int, int, int, Dict[int, AssetDTO], int]:
        if self._current_query is None:
            return 0, 0, -1, {}, self._collection_revision

        active_root = self._active_root or self._library_root
        if active_root is None or self._asset_query_service is None:
            return 0, 0, -1, {}, self._collection_revision

        read_window = getattr(self._asset_query_service, "read_query_asset_window", None)
        if callable(read_window):
            window_first, window_limit = self._compute_target_window_unbounded(first, last)
            raw_window_first = self._pending_adjusted_raw_offset_for_view_offset(
                self._current_query,
                window_first,
            )
            try:
                window = read_window(
                    active_root,
                    self._current_query,
                    raw_window_first,
                    window_limit,
                )
            except Exception as exc:
                emit_perf_event(
                    "gallery_window_fetch_failed",
                    first=window_first,
                    last=window_first + max(0, window_limit) - 1,
                    error=type(exc).__name__,
                )
                return self._total_count, window_first, window_first + max(0, window_limit) - 1, {}, self._collection_revision
            pending_source_count = self._pending_source_count_for_query(
                self._current_query
            )
            extra_limit = pending_source_count if pending_source_count > 0 else 0
            if extra_limit > 0:
                try:
                    window = read_window(
                        active_root,
                        self._current_query,
                        raw_window_first,
                        window_limit + extra_limit,
                    )
                except Exception:
                    pass
            rows = self._rows_to_dtos(window_first, window.rows)
            total_count = self._pending_adjusted_total_count(
                window.total_count,
                self._current_query,
                rows.values(),
            )
            fetched_last = window_first + len(window.rows) - 1
            if not rows and window.total_count > 0:
                fetched_last = window_first + max(0, window_limit) - 1
            return (
                total_count,
                window_first,
                min(max(window_first, fetched_last), max(0, total_count - 1)),
                rows,
                window.collection_revision,
            )

        count_query = self._count_query(self._current_query)
        new_total = self._pending_adjusted_total_count(
            self._asset_query_service.count_query_assets(count_query),
            count_query,
            (),
        )
        if new_total <= 0:
            return 0, 0, -1, {}, self._collection_revision
        bounded_first = max(0, min(first, new_total - 1))
        bounded_last = max(bounded_first, min(last, new_total - 1))
        window_first, window_last = self._compute_target_window(
            bounded_first,
            bounded_last,
            new_total,
        )
        return (
            new_total,
            window_first,
            window_last,
            self._fetch_rows(window_first, window_last),
            self._collection_revision + 1,
        )

    def _compute_target_window_unbounded(self, first: int, last: int) -> tuple[int, int]:
        first = max(0, first)
        last = max(first, last)
        visible_count = max(1, last - first + 1)
        target_size = min(
            self.MAX_WINDOW_SIZE,
            max(self.MIN_WINDOW_SIZE, visible_count * self.WINDOW_MULTIPLIER),
        )
        if visible_count >= target_size:
            return first, target_size

        lookbehind = visible_count * self.LOOKBEHIND_SCREENS
        window_first = max(0, first - lookbehind)
        return window_first, target_size

    def _compute_target_window(self, first: int, last: int, total_count: int) -> tuple[int, int]:
        visible_count = max(1, last - first + 1)
        target_size = min(
            self.MAX_WINDOW_SIZE,
            max(self.MIN_WINDOW_SIZE, visible_count * self.WINDOW_MULTIPLIER),
        )
        if visible_count >= target_size:
            window_first = max(0, min(first, total_count - 1))
            window_last = min(total_count - 1, window_first + target_size - 1)
            if window_last - window_first + 1 < target_size:
                window_first = max(0, window_last - target_size + 1)
            return window_first, window_last

        lookbehind = visible_count * self.LOOKBEHIND_SCREENS
        lookahead = visible_count * self.LOOKAHEAD_SCREENS

        window_first = max(0, first - lookbehind)
        window_last = min(total_count - 1, last + lookahead)
        current_size = max(0, window_last - window_first + 1)
        deficit = max(0, target_size - current_size)
        if deficit > 0:
            extend_ahead = min(total_count - 1 - window_last, deficit)
            window_last += extend_ahead
            deficit -= extend_ahead
            if deficit > 0:
                window_first = max(0, window_first - deficit)
        if window_last - window_first + 1 > self.MAX_WINDOW_SIZE:
            window_last = min(total_count - 1, window_first + self.MAX_WINDOW_SIZE - 1)
        return window_first, window_last

    def _window_needs_reload(self, first: int, last: int) -> bool:
        if self._window_range is None:
            return True

        window_first, window_last = self._window_range
        if first < window_first or last > window_last:
            return True

        window_size = max(1, window_last - window_first + 1)
        margin = max(1, int(window_size * self.HYSTERESIS_RATIO))
        safe_first = window_first + margin
        safe_last = window_last - margin
        if first < safe_first or last > safe_last:
            return True

        for row in range(first, last + 1):
            if row == self._pinned_row:
                continue
            if row not in self._row_cache:
                return True
        return False

    def _fetch_rows(self, first: int, last: int) -> Dict[int, AssetDTO]:
        if self._current_query is None or last < first:
            return {}

        pending_source_count = self._pending_source_count_for_query(self._current_query)
        raw_first = self._pending_adjusted_raw_offset_for_view_offset(
            self._current_query,
            first,
        )
        query = self._slice_query(
            self._current_query,
            raw_first,
            last - first + 1 + pending_source_count,
        )
        validate_paths = self._should_validate_paths(self._current_query)
        rows: Dict[int, AssetDTO] = {}
        active_root = self._active_root or self._library_root
        if active_root is None or self._asset_query_service is None:
            return {}
        try:
            rows = self._rows_to_dtos(
                first,
                self._asset_query_service.read_query_asset_rows(active_root, query),
                active_root=active_root,
                validate_paths=validate_paths,
            )
        except Exception as exc:
            emit_perf_event(
                "gallery_window_fetch_failed",
                first=first,
                last=last,
                error=type(exc).__name__,
            )
            return {}
        return rows

    def _rows_to_dtos(
        self,
        first: int,
        raw_rows: Iterable[dict],
        *,
        active_root: Path | None = None,
        validate_paths: bool | None = None,
    ) -> Dict[int, AssetDTO]:
        rows: Dict[int, AssetDTO] = {}
        root = active_root or self._active_root or self._library_root
        if root is None:
            return rows
        if validate_paths is None:
            validate_paths = (
                self._current_query is not None
                and self._should_validate_paths(self._current_query)
            )
        for offset, row in enumerate(raw_rows):
            view_rel = row.get("rel") if isinstance(row, dict) else None
            if not isinstance(view_rel, str) or not view_rel:
                continue
            if self._scan_row_is_thumbnail(view_rel, row):
                continue
            dto = self._scan_row_to_dto(root, view_rel, row)
            if dto is None:
                continue
            if validate_paths and not self._path_exists_cached(dto.abs_path):
                continue
            if self._dto_matches_pending_source(dto):
                continue
            row_index = first + len(rows)
            rows[row_index] = dto
        return rows

    def _fetch_single_row(self, row: int) -> Optional[AssetDTO]:
        if self._current_query is None or row < 0:
            return None
        fetched = self._fetch_rows(row, row)
        return fetched.get(row)

    def _request_visible_thumbnail_backfill(self, first: int, last: int) -> int:
        if self._current_query is None or self._asset_query_service is None:
            return 0
        active_root = self._active_root or self._library_root
        if active_root is None:
            return 0
        window_first, window_limit = self._compute_target_window_unbounded(first, last)
        request_key = (window_first, window_limit)
        window_end = window_first + window_limit
        for requested_first, requested_limit in self._thumbnail_backfill_windows:
            if requested_first <= window_first and requested_first + requested_limit >= window_end:
                return 0
        if request_key in self._thumbnail_backfill_windows:
            return 0
        request_backfill = getattr(self._asset_query_service, "request_thumbnail_backfill", None)
        if not callable(request_backfill):
            return 0
        self._thumbnail_backfill_windows.add(request_key)
        try:
            queued = int(request_backfill(active_root, self._current_query, window_first, window_limit) or 0)
            if queued > 0:
                self._thumbnail_backfill_pending = True
                self.thumbnail_backfill_scheduled.emit()
            return queued
        except Exception as exc:
            emit_perf_event(
                "gallery_thumbnail_backfill_failed",
                first=window_first,
                limit=window_limit,
                error=type(exc).__name__,
            )
            return 0

    def _ensure_row_loaded(self, row: int, *, emit_signals: bool) -> None:
        if row in self._row_cache:
            return
        dto = self._fetch_single_row(row)
        if dto is None:
            return
        self._row_cache[row] = dto
        if emit_signals:
            self.window_changed.emit(row, row)

    def _map_scan_rows_to_active_entries(self, scan_root: Path, chunk: List[dict]) -> list[tuple[str, dict]]:
        if self._active_root is None:
            return []
        try:
            scan_root_resolved = scan_root.resolve()
            view_root_resolved = self._active_root.resolve()
        except OSError:
            return []

        is_direct_match = scan_root_resolved == view_root_resolved
        is_scan_parent = scan_root_resolved in view_root_resolved.parents
        is_scan_child = view_root_resolved in scan_root_resolved.parents
        if not (is_direct_match or is_scan_parent or is_scan_child):
            return []

        mapped: list[tuple[str, dict]] = []
        for row in chunk:
            raw_rel = row.get("rel")
            if not isinstance(raw_rel, str) or not raw_rel:
                continue
            view_rel = self._resolve_view_rel(
                raw_rel,
                scan_root_resolved,
                view_root_resolved,
            )
            if view_rel:
                mapped.append((view_rel, row))
        return mapped

    def _pending_scan_affects_visible_window(self, first: int, last: int) -> bool:
        return self._window_rel_intersects_pending_scan(first, last) or self._scan_sort_keys_affect_visible_window(first, last)

    def _window_rel_intersects_pending_scan(self, first: int, last: int) -> bool:
        if not self._pending_scan_rels:
            return False
        for row in range(first, last + 1):
            dto = self._row_cache.get(row)
            if dto is None:
                continue
            if dto.rel_path.as_posix() in self._pending_scan_rels:
                return True
        return False

    def _scan_sort_keys_affect_visible_window(self, first: int, last: int) -> bool:
        if not self._pending_scan_sort_keys:
            return False

        bounds = self._visible_window_sort_bounds(first, last)
        if bounds is None:
            return False

        top_key, bottom_key = bounds
        for key in self._pending_scan_sort_keys:
            if top_key >= key >= bottom_key:
                return True
        return False

    def _visible_window_sort_bounds(self, first: int, last: int) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
        top_key: Optional[tuple[str, str]] = None
        bottom_key: Optional[tuple[str, str]] = None
        for row in range(first, last + 1):
            dto = self._row_cache.get(row)
            if dto is None:
                continue
            sort_key = self._sort_key_from_dto(dto)
            if sort_key is None:
                continue
            if top_key is None:
                top_key = sort_key
            bottom_key = sort_key
        if top_key is None or bottom_key is None:
            return None
        return top_key, bottom_key

    def _sort_key_from_dto(self, dto: AssetDTO) -> Optional[tuple[str, str]]:
        if dto.created_at is None:
            return None
        return dto.created_at.isoformat(), str(dto.id or "")

    def _sort_key_from_scan_row(self, row: dict) -> Optional[tuple[str, str]]:
        dt_raw = row.get("dt")
        if not isinstance(dt_raw, str) or not dt_raw:
            return None
        return dt_raw, str(row.get("id") or "")

    def _scan_root_matches_active_root(self, scan_root: Path) -> bool:
        if self._active_root is None:
            return False
        try:
            scan_root_resolved = scan_root.resolve()
            view_root_resolved = self._active_root.resolve()
        except OSError:
            return False
        return (
            scan_root_resolved == view_root_resolved
            or scan_root_resolved in view_root_resolved.parents
            or view_root_resolved in scan_root_resolved.parents
        )

    def _resolve_view_rel(
        self,
        raw_rel: str,
        scan_root: Path,
        view_root: Path,
    ) -> Optional[str]:
        for candidate in self._scan_row_abs_path_candidates(raw_rel, scan_root):
            try:
                return candidate.relative_to(view_root).as_posix()
            except ValueError:
                continue
        return None

    def _scan_row_abs_path_candidates(
        self,
        raw_rel: str,
        scan_root: Path,
    ) -> list[Path]:
        candidates: list[Path] = []
        if self._library_root is not None:
            library_candidate = self._library_root.resolve() / raw_rel
            scan_candidate = scan_root / raw_rel
            if _path_is_relative_to(library_candidate, scan_root):
                candidates.extend([library_candidate, scan_candidate])
            else:
                candidates.extend([scan_candidate, library_candidate])
        else:
            candidates.append(scan_root / raw_rel)

        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _geotagged_asset_to_dto(self, asset: object, library_root: Path) -> Optional[AssetDTO]:
        return _geotagged_asset_to_dto_fn(asset, library_root)

    def _scan_row_to_dto(
        self,
        view_root: Path,
        view_rel: str,
        row: dict,
    ) -> Optional[AssetDTO]:
        return _scan_row_to_dto_fn(view_root, view_rel, row)

    def _normalize_abs_key(self, path: Path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    @staticmethod
    def _normalize_rel_key(path: Path) -> str:
        return os.path.normcase(Path(path).as_posix())

    def _resolve_abs_path(self, rel_path: Path) -> Path:
        return _resolve_abs_path_fn(rel_path, self._library_root)

    def _path_exists_cached(self, path: Path) -> bool:
        return self._path_cache.exists_cached(path)

    def _should_validate_paths(self, query: AssetQuery) -> bool:
        return _should_validate_paths_fn(query, self._library_root)

    def _is_legacy_thumb_path(self, rel_path: Path) -> bool:
        return _is_legacy_thumb_path_fn(rel_path)

    def _scan_row_is_thumbnail(self, rel: str, row: dict) -> bool:
        return _scan_row_is_thumbnail_fn(rel, row)

    def _should_include_pending(self, pending: _PendingMove, query: AssetQuery) -> bool:
        return _should_include_pending_fn(pending, query)

    def _pending_source_matches_query(self, pending: _PendingMove, query: AssetQuery) -> bool:
        return _pending_source_matches_query_fn(pending, query)

    def _pending_source_count_for_query(self, query: AssetQuery) -> int:
        return sum(
            1
            for pending in self._pending_moves
            if self._pending_source_matches_query(pending, query)
        )

    def _pending_source_rows_for_query(self, query: AssetQuery) -> list[int]:
        if self._asset_query_service is None or not self._pending_moves:
            return []
        find_row_by_path = getattr(self._asset_query_service, "find_row_by_path", None)
        if not callable(find_row_by_path):
            return []
        rows: list[int] = []
        for pending in self._pending_moves:
            if not self._pending_source_matches_query(pending, query):
                continue
            raw_row = find_row_by_path(query, pending.source_abs)
            if raw_row is None:
                raw_row = find_row_by_path(query, pending.source_library_rel)
            if raw_row is not None:
                rows.append(int(raw_row))
        return sorted(set(rows))

    def _pending_adjusted_raw_offset_for_view_offset(
        self,
        query: AssetQuery,
        view_offset: int,
    ) -> int:
        raw_offset = max(0, int(view_offset))
        for source_row in self._pending_source_rows_for_query(query):
            if source_row <= raw_offset:
                raw_offset += 1
                continue
            break
        return raw_offset

    def _pending_adjusted_view_row_for_raw_row(
        self,
        query: AssetQuery,
        raw_row: int,
    ) -> int:
        hidden_before = sum(
            1
            for source_row in self._pending_source_rows_for_query(query)
            if source_row < raw_row
        )
        return max(0, int(raw_row) - hidden_before)

    def _pending_adjusted_total_count(
        self,
        raw_total_count: int,
        query: AssetQuery,
        existing_rows: Iterable[AssetDTO],
    ) -> int:
        pending_sources = self._pending_source_count_for_query(query)
        pending_insertions = len(
            self._pending_insertions_for_query(query, existing_rows)
        )
        return max(0, int(raw_total_count) - pending_sources) + pending_insertions

    def _pending_insertions_for_query(
        self,
        query: AssetQuery,
        existing_rows: Iterable[AssetDTO],
    ) -> list[AssetDTO]:
        if not self._pending_moves:
            return []
        existing_keys: set[str] = set()
        for dto in existing_rows:
            existing_keys.update(self._dto_destination_keys(dto, include_id=True))
        inserted: list[AssetDTO] = []
        for pending in self._pending_moves:
            if not self._should_include_pending(pending, query):
                continue
            dto = pending.dto
            pending_keys = self._pending_destination_keys(pending, include_id=True)
            if existing_keys.intersection(pending_keys):
                continue
            inserted.append(dto)
            existing_keys.update(pending_keys)
        return inserted

    def _dto_matches_pending_source(self, dto: AssetDTO) -> bool:
        if not self._pending_paths:
            return False
        dto_abs_key = self._normalize_abs_key(dto.abs_path)
        dto_rel_key = self._normalize_rel_key(self._rel_path_for_abs(dto.abs_path))
        dto_fallback_rel_key = self._normalize_rel_key(dto.rel_path)
        dto_id = str(dto.id) if dto.id else None
        return any(
            not self._dto_matches_pending_destination(dto, pending)
            and (
                dto_abs_key == self._normalize_abs_key(pending.source_abs)
                or dto_rel_key == self._normalize_rel_key(pending.source_library_rel)
                or dto_fallback_rel_key == self._normalize_rel_key(
                    pending.source_library_rel
                )
                or (
                    dto_id is not None
                    and pending.source_id is not None
                    and dto_id == pending.source_id
                )
            )
            for pending in self._pending_moves
        )

    def _dto_matches_pending_destination(
        self,
        dto: AssetDTO,
        pending: _PendingMove,
    ) -> bool:
        return bool(
            self._dto_destination_keys(dto).intersection(
                self._pending_destination_keys(pending)
            )
        )

    def _dto_destination_keys(
        self,
        dto: AssetDTO,
        *,
        include_id: bool = False,
    ) -> set[str]:
        keys = {
            f"abs:{self._normalize_abs_key(dto.abs_path)}",
            f"rel:{self._normalize_rel_key(self._rel_path_for_abs(dto.abs_path))}",
            f"rel:{self._normalize_rel_key(dto.rel_path)}",
        }
        if include_id and dto.id:
            keys.add(f"id:{dto.id}")
        return keys

    def _pending_destination_keys(
        self,
        pending: _PendingMove,
        *,
        include_id: bool = False,
    ) -> set[str]:
        keys = {
            f"abs:{self._normalize_abs_key(pending.destination_abs)}",
            f"rel:{self._normalize_rel_key(pending.destination_rel)}",
        }
        if include_id and pending.dto.id:
            keys.add(f"id:{pending.dto.id}")
        return keys

    def _album_path_for_root(self, root: Path) -> str:
        if self._library_root is None:
            return root.name
        try:
            rel = root.resolve().relative_to(self._library_root.resolve())
        except (OSError, ValueError):
            try:
                rel = root.relative_to(self._library_root)
            except ValueError:
                return root.name
        return rel.as_posix()

    @staticmethod
    def _album_path_for_rel(rel_path: Path) -> str:
        parent = rel_path.parent
        rel = parent.as_posix()
        return "" if rel == "." else rel

    def _rel_path_for_abs(self, path: Path) -> Path:
        if self._library_root is None:
            return Path(path.name)
        try:
            return path.resolve().relative_to(self._library_root.resolve())
        except (OSError, ValueError):
            try:
                return path.relative_to(self._library_root)
            except ValueError:
                return Path(path.name)

    def _slice_query(self, query: AssetQuery, offset: int, limit: int) -> AssetQuery:
        sliced = self._clone_query(query)
        sliced.offset = offset
        sliced.limit = max(0, limit)
        return sliced

    def _count_query(self, query: AssetQuery) -> AssetQuery:
        counted = self._clone_query(query)
        counted.limit = None
        counted.offset = 0
        return counted

    @staticmethod
    def _clone_query(query: AssetQuery) -> AssetQuery:
        return copy.deepcopy(query)

    def _iter_cached_rows(self) -> List[tuple[int, AssetDTO]]:
        return sorted(self._row_cache.items(), key=lambda item: item[0])

    def _reset_window_state(self, *, clear_pending: bool = False) -> None:
        self._row_cache.clear()
        self._total_count = 0
        self._window_range = None
        self._visible_range = None
        self._warm_range = None
        self._pinned_row = None
        self._path_cache.clear()
        if clear_pending:
            self._pending_moves.clear()
            self._pending_paths.clear()
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()
        self._thumbnail_backfill_windows.clear()
        self._thumbnail_backfill_pending = False
        self._pending_row_loads.clear()
        self._pending_window_generations.clear()
        self._demand_generation = 0
        self._collection_revision += 1
        self._request_generation += 1
