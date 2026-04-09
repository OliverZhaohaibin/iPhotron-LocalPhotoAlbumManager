"""Pure Python collection store backing gallery and filmstrip selection."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Dict, List, Optional

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models import Asset
from iPhoto.domain.models.query import AssetQuery
from iPhoto.domain.repositories import IAssetRepository
from iPhoto.gui.viewmodels.signal import Signal

from iPhoto.gui.viewmodels.asset_dto_converter import (
    geotagged_asset_to_dto as _geotagged_asset_to_dto_fn,
    is_legacy_thumb_path as _is_legacy_thumb_path_fn,
    is_thumbnail_asset as _is_thumbnail_asset_fn,
    resolve_abs_path as _resolve_abs_path_fn,
    scan_row_is_thumbnail as _scan_row_is_thumbnail_fn,
    to_dto as _to_dto_fn,
)
from iPhoto.gui.viewmodels.asset_paging import (
    should_validate_paths as _should_validate_paths_fn,
)
from iPhoto.gui.viewmodels.path_cache import PathExistsCache
from iPhoto.gui.viewmodels.pending_move_buffer import (
    _PendingMove,
    should_include_pending as _should_include_pending_fn,
)


class GalleryCollectionStore:
    """Pure Python gallery data store with viewport-aware caching."""

    INITIAL_VISIBLE_ROWS = 80
    MIN_WINDOW_SIZE = 300
    MAX_WINDOW_SIZE = 2000
    WINDOW_MULTIPLIER = 4
    LOOKBEHIND_SCREENS = 1
    LOOKAHEAD_SCREENS = 2
    HYSTERESIS_RATIO = 0.25

    def __init__(
        self,
        repository: IAssetRepository,
        library_root: Optional[Path] = None,
    ) -> None:
        self.data_changed = Signal()
        self.window_changed = Signal()
        self.count_changed = Signal()
        self.row_changed = Signal()

        self._repo = repository
        self._library_root = library_root
        self._current_query: Optional[AssetQuery] = None
        self._selection_query: Optional[AssetQuery] = None
        self._selection_direct_assets: Optional[list] = None
        self._selection_library_root: Optional[Path] = library_root
        self._total_count = 0
        self._row_cache: Dict[int, AssetDTO] = {}
        self._window_range: Optional[tuple[int, int]] = None
        self._visible_range: Optional[tuple[int, int]] = None
        self._active_root: Optional[Path] = None
        self._path_cache = PathExistsCache()
        self._pending_moves: List[_PendingMove] = []
        self._pending_paths: set[str] = set()
        self._pinned_row: Optional[int] = None
        self._pending_scan_refresh = False
        self._pending_scan_rels: set[str] = set()
        self._pending_scan_sort_keys: set[tuple[str, str]] = set()
        self._direct_mode = False

    def set_library_root(self, root: Optional[Path]) -> None:
        if self._library_root == root:
            return
        self._library_root = root
        self._reset_window_state()
        self.data_changed.emit()

    def set_repository(self, repo: IAssetRepository) -> None:
        if self._repo is repo:
            return
        self._repo = repo
        self._reset_window_state()
        self.data_changed.emit()

    def rebind_repository(self, repo: IAssetRepository, library_root: Optional[Path]) -> None:
        self.set_repository(repo)
        self.set_library_root(library_root)

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
        self._load_initial_window()
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
            if dto is not None:
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
        if dto is not None or self._direct_mode:
            return dto
        if self._visible_range is not None:
            visible_first, visible_last = self._visible_range
            if visible_first <= index <= visible_last:
                self._ensure_pinned_row_loaded(index, emit_signals=False)
                return self._row_cache.get(index)
        if self._pinned_row == index:
            self._ensure_pinned_row_loaded(index, emit_signals=False)
            return self._row_cache.get(index)
        return None

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
                if dto is not None and self._normalize_abs_key(dto.abs_path) == target:
                    return dto
        return None

    def row_for_path(self, path: Path) -> Optional[int]:
        target = self._normalize_abs_key(path)
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
                if dto is None:
                    continue
                if self._normalize_abs_key(dto.abs_path) == target:
                    return next_index
                next_index += 1
            return None

        if self._current_query is None or self._total_count <= 0:
            return None

        batch_size = 200
        for offset in range(0, self._total_count, batch_size):
            batch = self._fetch_rows(offset, min(self._total_count - 1, offset + batch_size - 1))
            for row, dto in batch.items():
                if self._normalize_abs_key(dto.abs_path) == target:
                    self._row_cache.setdefault(row, dto)
                    return row
        return None

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

        self._row_cache = new_cache
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
            str(dto.abs_path): (idx, dto) for idx, dto in self._iter_cached_rows()
        }
        for path in paths:
            key = str(path)
            if key in self._pending_paths:
                continue
            found = cached_map.get(key)
            if found is None:
                continue
            row, dto = found
            removed_rows.append(row)
            destination_abs = destination_root / path.name
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
                source_abs=path,
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
            self._load_initial_window()
            if self._total_count == 0:
                return

        first = max(0, first)
        last = max(first, last)
        if self._total_count > 0:
            last = min(last, self._total_count - 1)
        self._visible_range = (first, last)

        should_refresh = self._pending_scan_refresh and (
            first == 0 or self._window_rel_intersects_pending_scan(first, last)
        )
        if should_refresh or self._window_needs_reload(first, last):
            self._reload_window_for_visible_range(first, last, emit_signals=True)

    def pin_row(self, row: int) -> None:
        if row < 0 or row >= self._total_count:
            self._pinned_row = None
            return
        self._pinned_row = row
        self._ensure_pinned_row_loaded(row, emit_signals=True)

    def handle_scan_chunk(self, scan_root: Path, chunk: List[dict]) -> None:
        if not chunk or self._current_query is None or self._active_root is None:
            return
        mapped_entries = self._map_scan_rows_to_active_entries(scan_root, chunk)
        if not mapped_entries:
            return

        self._pending_scan_rels.update(view_rel for view_rel, _row in mapped_entries)
        self._pending_scan_sort_keys.update(
            sort_key
            for _view_rel, row in mapped_entries
            for sort_key in [self._sort_key_from_scan_row(row)]
            if sort_key is not None
        )
        self._pending_scan_refresh = True

        if self._visible_range is None:
            return

        visible_first, visible_last = self._visible_range
        if visible_first == 0 or self._pending_scan_affects_visible_window(visible_first, visible_last):
            self._flush_pending_scan_refresh()

    def handle_scan_finished(self, root: Path, success: bool) -> None:
        if not success or self._current_query is None or self._active_root is None:
            return
        if not self._scan_root_matches_active_root(root):
            return
        self._pending_scan_refresh = True
        if self._visible_range is not None and self._visible_range[0] == 0:
            self._flush_pending_scan_refresh()

    def _flush_pending_scan_refresh(self) -> None:
        if not self._pending_scan_refresh or self._current_query is None:
            self._pending_scan_refresh = False
            self._pending_scan_rels.clear()
            self._pending_scan_sort_keys.clear()
            return
        if self._visible_range is None:
            self._pending_scan_refresh = False
            self._pending_scan_rels.clear()
            self._pending_scan_sort_keys.clear()
            return
        first, last = self._visible_range
        if first != 0 and not self._pending_scan_affects_visible_window(first, last):
            return
        self._reload_window_for_visible_range(first, last, emit_signals=True)

    def _load_initial_window(self) -> None:
        visible_last = max(0, self.INITIAL_VISIBLE_ROWS - 1)
        self._visible_range = (0, visible_last)
        self._reload_window_for_visible_range(0, visible_last, emit_signals=False)

    def _reload_window_for_visible_range(self, first: int, last: int, *, emit_signals: bool) -> None:
        if self._current_query is None:
            return

        count_query = self._count_query(self._current_query)
        new_total = self._repo.count(count_query)
        if new_total <= 0:
            old_total = self._total_count
            self._reset_window_state()
            if emit_signals and old_total != 0:
                self.count_changed.emit(old_total, 0)
                self.data_changed.emit()
            return

        first = max(0, min(first, new_total - 1))
        last = max(first, min(last, new_total - 1))
        window_first, window_last = self._compute_target_window(first, last, new_total)
        fetched_rows = self._fetch_rows(window_first, window_last)

        old_total = self._total_count
        previous_cache = self._row_cache
        new_cache = dict(fetched_rows)

        pinned_row = self._pinned_row
        if pinned_row is not None and pinned_row not in new_cache and 0 <= pinned_row < new_total:
            pinned_dto = previous_cache.get(pinned_row)
            if pinned_dto is None:
                pinned_dto = self._fetch_single_row(pinned_row)
            if pinned_dto is not None:
                new_cache[pinned_row] = pinned_dto

        self._row_cache = new_cache
        self._total_count = new_total
        self._window_range = (window_first, window_last)
        self._visible_range = (first, last)
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()

        if emit_signals:
            if old_total != new_total:
                self.count_changed.emit(old_total, new_total)
            self.window_changed.emit(window_first, window_last)
            if pinned_row is not None and pinned_row not in fetched_rows and pinned_row in self._row_cache:
                self.window_changed.emit(pinned_row, pinned_row)
            self.data_changed.emit()

    def _compute_target_window(self, first: int, last: int, total_count: int) -> tuple[int, int]:
        visible_count = max(1, last - first + 1)
        target_size = min(
            self.MAX_WINDOW_SIZE,
            max(self.MIN_WINDOW_SIZE, visible_count * self.WINDOW_MULTIPLIER),
        )
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

        query = self._slice_query(self._current_query, first, last - first + 1)
        validate_paths = self._should_validate_paths(self._current_query)
        rows: Dict[int, AssetDTO] = {}
        for offset, asset in enumerate(self._repo.find_by_query(query)):
            row_index = first + offset
            if self._is_thumbnail_asset(asset):
                continue
            abs_path = self._resolve_abs_path(asset.path)
            if validate_paths and not self._path_exists_cached(abs_path):
                continue
            rows[row_index] = self._to_dto(asset)
        return rows

    def _fetch_single_row(self, row: int) -> Optional[AssetDTO]:
        if self._current_query is None or row < 0:
            return None
        fetched = self._fetch_rows(row, row)
        return fetched.get(row)

    def _ensure_pinned_row_loaded(self, row: int, *, emit_signals: bool) -> None:
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
                is_scan_parent=is_scan_parent,
                is_scan_child=is_scan_child,
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
        *,
        is_scan_parent: bool,
        is_scan_child: bool,
    ) -> Optional[str]:
        if scan_root == view_root:
            return raw_rel
        if is_scan_parent:
            full_path = scan_root / raw_rel
            try:
                return full_path.relative_to(view_root).as_posix()
            except (OSError, ValueError):
                return None
        if is_scan_child:
            try:
                prefix = scan_root.relative_to(view_root).as_posix()
            except ValueError:
                return None
            prefix_slash = f"{prefix}/" if prefix else ""
            return f"{prefix_slash}{raw_rel}" if prefix_slash else raw_rel
        return None

    def _geotagged_asset_to_dto(self, asset: object, library_root: Path) -> Optional[AssetDTO]:
        return _geotagged_asset_to_dto_fn(asset, library_root)

    def _to_dto(self, asset: Asset) -> AssetDTO:
        return _to_dto_fn(asset, self._library_root)

    def _normalize_abs_key(self, path: Path) -> str:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        return os.path.normcase(str(resolved))

    def _resolve_abs_path(self, rel_path: Path) -> Path:
        return _resolve_abs_path_fn(rel_path, self._library_root)

    def _path_exists_cached(self, path: Path) -> bool:
        return self._path_cache.exists_cached(path)

    def _should_validate_paths(self, query: AssetQuery) -> bool:
        return _should_validate_paths_fn(query, self._library_root)

    def _is_legacy_thumb_path(self, rel_path: Path) -> bool:
        return _is_legacy_thumb_path_fn(rel_path)

    def _is_thumbnail_asset(self, asset: Asset) -> bool:
        return _is_thumbnail_asset_fn(asset)

    def _scan_row_is_thumbnail(self, rel: str, row: dict) -> bool:
        return _scan_row_is_thumbnail_fn(rel, row)

    def _should_include_pending(self, pending: _PendingMove, query: AssetQuery) -> bool:
        return _should_include_pending_fn(pending, query)

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

    def _reset_window_state(self) -> None:
        self._row_cache.clear()
        self._total_count = 0
        self._window_range = None
        self._visible_range = None
        self._pinned_row = None
        self._path_cache.clear()
        self._pending_moves.clear()
        self._pending_paths.clear()
        self._pending_scan_refresh = False
        self._pending_scan_rels.clear()
        self._pending_scan_sort_keys.clear()
