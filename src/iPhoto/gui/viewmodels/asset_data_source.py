import copy
import os
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal

from iPhoto.application.dtos import AssetDTO
from iPhoto.domain.models import Asset
from iPhoto.domain.models.query import AssetQuery
from iPhoto.domain.repositories import IAssetRepository

# Re-exports from extracted modules (backward compatibility)
from iPhoto.gui.viewmodels.path_cache import (  # noqa: F401
    PATH_EXISTS_CACHE_LIMIT,
    PathExistsCache,
)
from iPhoto.gui.viewmodels.pending_move_buffer import (  # noqa: F401
    _PendingMove,
    should_include_pending as _should_include_pending_fn,
)
from iPhoto.gui.viewmodels.asset_dto_converter import (  # noqa: F401
    THUMBNAIL_SUFFIX_RE,
    THUMBNAIL_MAX_DIMENSION,
    THUMBNAIL_MAX_BYTES,
    LEGACY_THUMB_DIRS,
    resolve_abs_path as _resolve_abs_path_fn,
    to_dto as _to_dto_fn,
    geotagged_asset_to_dto as _geotagged_asset_to_dto_fn,
    scan_row_to_dto as _scan_row_to_dto_fn,
    scan_row_matches_query as _scan_row_matches_query_fn,
    is_thumbnail_asset as _is_thumbnail_asset_fn,
    scan_row_is_thumbnail as _scan_row_is_thumbnail_fn,
    is_legacy_thumb_path as _is_legacy_thumb_path_fn,
)
from iPhoto.gui.viewmodels.asset_paging import (  # noqa: F401
    should_use_paging as _should_use_paging_fn,
    should_validate_paths as _should_validate_paths_fn,
)


class AssetDataSource(QObject):
    """Intermediary between Repository and ViewModel with viewport-driven caching."""

    dataChanged = Signal()
    windowChanged = Signal(int, int)
    countChanged = Signal(int, int)

    INITIAL_VISIBLE_ROWS = 80
    MIN_WINDOW_SIZE = 300
    MAX_WINDOW_SIZE = 2000
    WINDOW_MULTIPLIER = 4
    LOOKBEHIND_SCREENS = 1
    LOOKAHEAD_SCREENS = 2
    HYSTERESIS_RATIO = 0.25
    SCAN_REFRESH_DEBOUNCE_MS = 75

    def __init__(self, repository: IAssetRepository, library_root: Optional[Path] = None):
        super().__init__()
        self._repo = repository
        self._library_root = library_root
        self._current_query: Optional[AssetQuery] = None
        self._selection_query: Optional[AssetQuery] = None
        self._selection_direct_assets: Optional[list] = None
        self._selection_library_root: Optional[Path] = library_root
        self._total_count: int = 0
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
        self._scan_refresh_scheduled = False
        self._direct_mode = False

    def set_library_root(self, root: Optional[Path]):
        if self._library_root == root:
            return
        self._library_root = root
        self._reset_window_state()

    def set_repository(self, repo: IAssetRepository) -> None:
        if self._repo is repo:
            return
        self._repo = repo
        self._reset_window_state()

    def set_active_root(self, root: Optional[Path]) -> None:
        self._active_root = root

    def active_root(self) -> Optional[Path]:
        return self._active_root

    def library_root(self) -> Optional[Path]:
        """Return the configured library root for absolute-path resolution."""
        return self._library_root

    def load(self, query: AssetQuery):
        """Load data for the given query using a viewport-sized sparse cache."""
        self._selection_query = self._clone_query(query)
        self._selection_direct_assets = None
        self._selection_library_root = self._library_root
        self._current_query = self._clone_query(query)
        self._direct_mode = False
        self._reset_window_state()
        self._load_initial_window()

    def load_selection(
        self,
        active_root: Optional[Path],
        *,
        query: Optional[AssetQuery] = None,
        direct_assets: Optional[list] = None,
        library_root: Optional[Path] = None,
    ) -> None:
        """Load the current gallery selection through one coordinated entrypoint."""
        has_query = query is not None
        has_direct_assets = direct_assets is not None
        if has_query == has_direct_assets:
            raise ValueError("Exactly one of query or direct_assets must be provided.")

        self.set_active_root(active_root)
        if query is not None:
            self.load(query)
            return

        resolved_library_root = library_root or self._library_root or active_root
        if resolved_library_root is None:
            raise ValueError("library_root is required when loading direct assets.")
        self.load_geotagged_assets(list(direct_assets or []), resolved_library_root)

    def reload_current_query(self) -> None:
        if self._selection_direct_assets is not None:
            self.load_geotagged_assets(
                list(self._selection_direct_assets),
                self._selection_library_root or self._library_root,
            )
            return
        if self._selection_query is not None:
            self.load(self._selection_query)
            return
        if self._current_query is None:
            return
        self.load(self._current_query)

    def reload_current_selection(self) -> None:
        """Reload whichever selection is currently active."""
        self.reload_current_query()

    def load_geotagged_assets(self, assets: list, library_root: Path) -> None:
        """Load a pre-computed list of geotagged assets directly into the cache."""
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

    def _geotagged_asset_to_dto(self, asset, library_root: Path) -> Optional[AssetDTO]:
        return _geotagged_asset_to_dto_fn(asset, library_root)

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
        """Find a cached DTO by its absolute path."""
        target = self._normalize_abs_key(path)
        for dto in self._row_cache.values():
            if self._normalize_abs_key(dto.abs_path) == target:
                return dto
        return None

    def row_for_path(self, path: Path) -> Optional[int]:
        """Return the cached row index for *path* when available."""
        target = self._normalize_abs_key(path)
        for row, dto in self._row_cache.items():
            if self._normalize_abs_key(dto.abs_path) == target:
                return row
        return None

    def count(self) -> int:
        return self._total_count

    def update_favorite_status(self, row: int, is_favorite: bool):
        """Update the favorite status of the cached DTO at the given row."""
        dto = self._row_cache.get(row)
        if dto is not None:
            dto.is_favorite = is_favorite

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
            self.countChanged.emit(old_total, self._total_count)
            if self._total_count > 0:
                self.windowChanged.emit(max(0, removed[0] - 1), self._total_count - 1)
            self.dataChanged.emit()

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
        self._total_count += len(dtos)

    def prioritize_rows(self, first: int, last: int) -> None:
        """Prioritize the viewport range and refill the sparse cache around it."""
        if self._direct_mode:
            return
        if self._current_query is None:
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
        """Pin a row so detail/filmstrip access survives cache eviction."""
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
            self._schedule_scan_refresh()

    def handle_scan_finished(self, root: Path, success: bool) -> None:
        if not success or self._current_query is None or self._active_root is None:
            return
        if not self._scan_root_matches_active_root(root):
            return
        self._pending_scan_refresh = True
        if self._visible_range is not None and self._visible_range[0] == 0:
            self._schedule_scan_refresh()

    def _schedule_scan_refresh(self) -> None:
        if self._scan_refresh_scheduled:
            return
        app = QCoreApplication.instance()
        if app is None:
            self._scan_refresh_scheduled = False
            self._flush_pending_scan_refresh()
            return
        self._scan_refresh_scheduled = True
        QTimer.singleShot(self.SCAN_REFRESH_DEBOUNCE_MS, self._flush_pending_scan_refresh)

    def _flush_pending_scan_refresh(self) -> None:
        self._scan_refresh_scheduled = False
        if not self._pending_scan_refresh:
            return
        if self._current_query is None:
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

    def _reload_window_for_visible_range(
        self,
        first: int,
        last: int,
        *,
        emit_signals: bool,
    ) -> None:
        if self._current_query is None:
            return

        count_query = self._count_query(self._current_query)
        new_total = self._repo.count(count_query)
        if new_total <= 0:
            old_total = self._total_count
            self._reset_window_state()
            if emit_signals and old_total != 0:
                self.countChanged.emit(old_total, 0)
                self.dataChanged.emit()
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
                self.countChanged.emit(old_total, new_total)
            self.windowChanged.emit(window_first, window_last)
            if pinned_row is not None and pinned_row not in fetched_rows and pinned_row in self._row_cache:
                self.windowChanged.emit(pinned_row, pinned_row)
            self.dataChanged.emit()

    def _compute_target_window(
        self,
        first: int,
        last: int,
        total_count: int,
    ) -> tuple[int, int]:
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
            self.windowChanged.emit(row, row)

    def _map_scan_rows_to_active_entries(
        self,
        scan_root: Path,
        chunk: List[dict],
    ) -> list[tuple[str, dict]]:
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

    def _map_scan_rows_to_active_root(self, scan_root: Path, chunk: List[dict]) -> set[str]:
        return {
            view_rel for view_rel, _row in self._map_scan_rows_to_active_entries(scan_root, chunk)
        }

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

    def _visible_window_sort_bounds(
        self,
        first: int,
        last: int,
    ) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
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

    def _to_dto(self, asset: Asset) -> AssetDTO:
        return _to_dto_fn(asset, self._library_root)

    def _apply_pending_moves(self, query: AssetQuery) -> None:
        if not self._pending_moves:
            return
        next_row = self._total_count
        remaining: List[_PendingMove] = []
        existing_abs = {self._normalize_abs_key(dto.abs_path) for dto in self._row_cache.values()}
        for pending in self._pending_moves:
            if self._normalize_abs_key(pending.destination_abs) in existing_abs:
                self._pending_paths.discard(str(pending.source_abs))
                continue
            if not self._should_include_pending(pending, query):
                remaining.append(pending)
                continue
            self._row_cache[next_row] = pending.dto
            existing_abs.add(self._normalize_abs_key(pending.destination_abs))
            next_row += 1
            remaining.append(pending)
        self._pending_moves = remaining
        self._total_count = max(self._total_count, next_row)

    def _normalize_abs_key(self, path: Path) -> str:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        return os.path.normcase(str(resolved))

    def _resolve_abs_path(self, rel_path: Path) -> Path:
        return _resolve_abs_path_fn(rel_path, self._library_root)

    def _path_exists(self, path: Path) -> bool:
        return PathExistsCache.path_exists(path)

    def _path_cache_key(self, path: Path) -> str:
        return PathExistsCache.cache_key(path)

    def _path_exists_cached(self, path: Path) -> bool:
        return self._path_cache.exists_cached(path)

    def _should_validate_paths(self, query: AssetQuery) -> bool:
        return _should_validate_paths_fn(query, self._library_root)

    def _should_use_paging(self, query: AssetQuery) -> bool:
        return _should_use_paging_fn(query)

    def _is_legacy_thumb_path(self, rel_path: Path) -> bool:
        return _is_legacy_thumb_path_fn(rel_path)

    def _is_thumbnail_asset(self, asset: Asset) -> bool:
        return _is_thumbnail_asset_fn(asset)

    def _scan_row_is_thumbnail(self, rel: str, row: dict) -> bool:
        return _scan_row_is_thumbnail_fn(rel, row)

    def _should_include_pending(self, pending: _PendingMove, query: AssetQuery) -> bool:
        return _should_include_pending_fn(pending, query)

    def _find_cached_dto(self, path: Path) -> Optional[AssetDTO]:
        return self.find_dto_by_path(path)

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
        self._scan_refresh_scheduled = False
