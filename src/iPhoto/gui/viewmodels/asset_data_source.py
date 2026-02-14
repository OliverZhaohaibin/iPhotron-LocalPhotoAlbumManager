import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QThreadPool, Signal

from iPhoto.domain.models import Asset
from iPhoto.domain.models.query import AssetQuery
from iPhoto.domain.repositories import IAssetRepository
from iPhoto.application.dtos import AssetDTO

# ── Re-exports from extracted modules (backward compatibility) ───────────────
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
from iPhoto.gui.viewmodels.asset_workers import (  # noqa: F401
    _AssetLoadSignals,
    _AssetLoadWorker,
    _AssetPageSignals,
    _AssetPageWorker,
)
from iPhoto.gui.viewmodels.asset_paging import (  # noqa: F401
    should_use_paging as _should_use_paging_fn,
    should_validate_paths as _should_validate_paths_fn,
)


class AssetDataSource(QObject):
    """
    Intermediary between Repository and ViewModel.
    Handles paging logic and data fetching, and converts Domain Entities to DTOs.
    """

    dataChanged = Signal()

    def __init__(self, repository: IAssetRepository, library_root: Optional[Path] = None):
        super().__init__()
        self._repo = repository
        self._library_root = library_root
        self._current_query: Optional[AssetQuery] = None
        self._cached_dtos: List[AssetDTO] = []
        self._total_count: int = 0
        self._page_size = 1000
        self._pending_moves: List[_PendingMove] = []
        self._pending_paths: set[str] = set()
        self._active_root: Optional[Path] = None
        self._seen_abs_paths: set[str] = set()
        self._load_pool = QThreadPool.globalInstance()
        self._load_generation = 0
        self._paging_inflight = False
        self._paging_offset = 0
        self._paging_has_more = False
        self._path_cache = PathExistsCache()

    def set_library_root(self, root: Optional[Path]):
        if self._library_root == root:
            return
        self._library_root = root
        self._cached_dtos.clear()
        self._total_count = 0
        self._seen_abs_paths.clear()
        self._path_cache.clear()
        self.dataChanged.emit()

    def set_repository(self, repo: IAssetRepository) -> None:
        if self._repo is repo:
            return
        self._repo = repo
        self._cached_dtos.clear()
        self._total_count = 0
        self._seen_abs_paths.clear()
        self._path_cache.clear()
        self.dataChanged.emit()

    def set_active_root(self, root: Optional[Path]) -> None:
        self._active_root = root

    def library_root(self) -> Optional[Path]:
        """Return the configured library root for absolute-path resolution."""
        return self._library_root

    def load(self, query: AssetQuery):
        """Loads data for the given query."""
        self._current_query = query

        # Default limit if not set
        if not query.limit:
            query.limit = self._page_size if self._should_use_paging(query) else 5000

        # Reset cached data so the model does not expose stale assets
        self._cached_dtos.clear()
        self._total_count = 0
        self._seen_abs_paths.clear()
        self._path_cache.clear()
        self._paging_inflight = False
        self._paging_offset = 0
        self._paging_has_more = False
        self.dataChanged.emit()

        self._load_generation += 1
        generation = self._load_generation
        validate_paths = self._should_validate_paths(query)

        worker = _AssetLoadWorker(self, query, generation, validate_paths)
        worker.signals.completed.connect(self._on_load_completed)
        self._load_pool.start(worker)

    def _on_load_completed(self, generation: int, dtos: list[AssetDTO], raw_count: int) -> None:
        if generation != self._load_generation:
            return
        self._cached_dtos = dtos
        if self._current_query is not None:
            self._apply_pending_moves(self._current_query)
        self._total_count = len(self._cached_dtos)
        self._seen_abs_paths = {
            self._normalize_abs_key(dto.abs_path) for dto in self._cached_dtos
        }
        if self._current_query and self._current_query.limit:
            self._paging_has_more = raw_count >= self._current_query.limit
        else:
            self._paging_has_more = False
        self._paging_offset = raw_count
        self.dataChanged.emit()
        if self._current_query and self._should_use_paging(self._current_query):
            self._maybe_schedule_next_page()

    def reload_current_query(self) -> None:
        if self._current_query is None:
            return
        self.load(self._current_query)

    def load_geotagged_assets(self, assets: list, library_root: Path) -> None:
        """Load a pre-computed list of geotagged assets directly into the cache.

        This method enables O(1) cluster gallery opening by accepting assets
        that have already been aggregated during the map clustering phase,
        avoiding additional database queries.

        Args:
            assets: List of GeotaggedAsset objects from the map cluster.
            library_root: The library root path for resolving absolute paths.
        """
        from iPhoto.library.manager import GeotaggedAsset

        self._current_query = None  # No query for direct asset loading
        self._cached_dtos.clear()
        self._total_count = 0
        self._seen_abs_paths.clear()
        self._path_cache.clear()
        self._paging_inflight = False
        self._paging_offset = 0
        self._paging_has_more = False

        dtos: List[AssetDTO] = []
        for asset in assets:
            if not isinstance(asset, GeotaggedAsset):
                continue
            dto = self._geotagged_asset_to_dto(asset, library_root)
            if dto is not None:
                dtos.append(dto)

        self._cached_dtos = dtos
        self._total_count = len(self._cached_dtos)
        self._seen_abs_paths = {
            self._normalize_abs_key(dto.abs_path) for dto in self._cached_dtos
        }
        self.dataChanged.emit()

    def _geotagged_asset_to_dto(self, asset, library_root: Path) -> Optional[AssetDTO]:
        """Convert a GeotaggedAsset to an AssetDTO for display."""
        return _geotagged_asset_to_dto_fn(asset, library_root)

    def asset_at(self, index: int) -> Optional[AssetDTO]:
        if 0 <= index < len(self._cached_dtos):
            return self._cached_dtos[index]
        return None

    def find_dto_by_path(self, path: Path) -> Optional[AssetDTO]:
        """Find a cached DTO by its absolute path."""
        # Linear search is acceptable for small selections in restore/delete operations.
        # For larger datasets, we might need a hash map.
        resolved = str(path.resolve())
        for dto in self._cached_dtos:
            if str(dto.abs_path) == resolved:
                return dto
        return None

    def count(self) -> int:
        return len(self._cached_dtos)

    def update_favorite_status(self, row: int, is_favorite: bool):
        """Updates the favorite status of the cached DTO at the given row."""
        if 0 <= row < len(self._cached_dtos):
            dto = self._cached_dtos[row]
            dto.is_favorite = is_favorite

    def remove_rows(self, rows: List[int], *, emit: bool = True) -> None:
        if not rows:
            return
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self._cached_dtos):
                self._cached_dtos.pop(row)
        if emit:
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
        cached_map = {str(dto.abs_path): (idx, dto) for idx, dto in enumerate(self._cached_dtos)}
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
        self._cached_dtos.extend(dtos)
        for dto in dtos:
            self._seen_abs_paths.add(self._normalize_abs_key(dto.abs_path))
        self._total_count = len(self._cached_dtos)

    def handle_scan_chunk(self, scan_root: Path, chunk: List[dict]) -> None:
        if not chunk or self._current_query is None:
            return

        if self._active_root is None:
            return

        try:
            scan_root_resolved = scan_root.resolve()
            view_root_resolved = self._active_root.resolve()
        except OSError:
            return

        is_direct_match = scan_root_resolved == view_root_resolved
        is_scan_parent = scan_root_resolved in view_root_resolved.parents
        is_scan_child = view_root_resolved in scan_root_resolved.parents

        if not (is_direct_match or is_scan_parent or is_scan_child):
            return

        appended: List[AssetDTO] = []

        for row in chunk:
            raw_rel = row.get("rel")
            if not isinstance(raw_rel, str) or not raw_rel:
                continue
            if self._scan_row_is_thumbnail(raw_rel, row):
                continue

            view_rel = self._resolve_view_rel(
                raw_rel,
                scan_root_resolved,
                view_root_resolved,
                is_scan_parent=is_scan_parent,
                is_scan_child=is_scan_child,
            )
            if view_rel is None:
                continue

            dto = self._scan_row_to_dto(view_root_resolved, view_rel, row)
            if dto is None:
                continue
            if not self._path_exists(dto.abs_path):
                continue

            if not self._scan_row_matches_query(dto, row, self._current_query):
                continue

            abs_key = self._normalize_abs_key(dto.abs_path)
            if abs_key in self._seen_abs_paths:
                continue

            appended.append(dto)

        if not appended:
            return

        self.append_dtos(appended)
        self._total_count = len(self._cached_dtos)
        self.dataChanged.emit()

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
            except ValueError:
                return None
            except OSError:
                return None

        if is_scan_child:
            try:
                prefix = scan_root.relative_to(view_root).as_posix()
            except ValueError:
                return None
            prefix_slash = f"{prefix}/" if prefix else ""
            return f"{prefix_slash}{raw_rel}" if prefix_slash else raw_rel

        return None

    def _scan_row_to_dto(
        self,
        view_root: Path,
        view_rel: str,
        row: dict,
    ) -> Optional[AssetDTO]:
        return _scan_row_to_dto_fn(view_root, view_rel, row)

    def _scan_row_matches_query(
        self,
        dto: AssetDTO,
        row: dict,
        query: AssetQuery,
    ) -> bool:
        return _scan_row_matches_query_fn(dto, row, query)

    def _to_dto(self, asset: Asset) -> AssetDTO:
        return _to_dto_fn(asset, self._library_root)

    def _apply_pending_moves(self, query: AssetQuery) -> None:
        if not self._pending_moves:
            return
        updated = False
        existing_abs = {self._normalize_abs_key(dto.abs_path) for dto in self._cached_dtos}
        remaining: List[_PendingMove] = []
        for pending in self._pending_moves:
            if self._normalize_abs_key(pending.destination_abs) in existing_abs:
                updated = True
                self._pending_paths.discard(str(pending.source_abs))
                continue
            if not self._should_include_pending(pending, query):
                remaining.append(pending)
                continue
            self._cached_dtos.append(pending.dto)
            existing_abs.add(str(pending.destination_abs))
            updated = True
            remaining.append(pending)
        if updated:
            self._pending_moves = remaining

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

    def _maybe_schedule_next_page(self) -> None:
        if self._paging_inflight:
            return
        if self._current_query is None:
            return
        if not self._current_query.limit:
            return
        if not self._paging_has_more:
            return
        self._paging_inflight = True
        generation = self._load_generation
        validate_paths = self._should_validate_paths(self._current_query)
        worker = _AssetPageWorker(
            self,
            self._current_query,
            generation,
            self._paging_offset,
            validate_paths,
        )
        worker.signals.completed.connect(self._on_page_loaded)
        self._load_pool.start(worker)

    def _on_page_loaded(
        self,
        generation: int,
        offset: int,
        dtos: list[AssetDTO],
        raw_count: int,
    ) -> None:
        if generation != self._load_generation:
            return
        self._paging_inflight = False
        if self._current_query and self._current_query.limit:
            self._paging_has_more = raw_count >= self._current_query.limit
        else:
            self._paging_has_more = False
        if raw_count == 0:
            return
        self._paging_offset = offset + raw_count
        if dtos:
            self.append_dtos(dtos)
            self.dataChanged.emit()
        if self._paging_has_more:
            self._maybe_schedule_next_page()

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
        for dto in self._cached_dtos:
            if dto.abs_path == path:
                return dto
        return None

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
