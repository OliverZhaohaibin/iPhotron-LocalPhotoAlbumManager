"""List model combining ``index.jsonl`` and ``links.json`` data."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSize,
    Qt,
    Slot,
    QTimer,
    Signal,
)
from PySide6.QtGui import QPixmap

from ..tasks.thumbnail_loader import ThumbnailLoader
from .asset_cache_manager import AssetCacheManager
from .asset_data_loader import AssetDataLoader
from .asset_state_manager import AssetListStateManager
from .asset_row_adapter import AssetRowAdapter
from .roles import Roles, role_names
from ....utils.pathutils import normalise_for_compare, is_descendant_path

# Modular components
from .asset_list import (
    AssetDataOrchestrator,
    ModelFilterHandler,
    IncrementalUpdateHandler,
    AssetPathResolver,
    OptimisticTransactionManager,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...facade import AppFacade


logger = logging.getLogger(__name__)


class AssetListModel(QAbstractListModel):
    """Expose album assets to Qt views using a modular architecture."""

    # Signals required by views
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)

    def __init__(self, facade: "AppFacade", parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._facade = facade
        self._album_root: Optional[Path] = None
        self._thumb_size = QSize(512, 512)

        # 1. State & Cache Management (Legacy/Core)
        # Try to acquire library root early if available
        library_root = None
        if self._facade.library_manager:
            library_root = self._facade.library_manager.root()

        self._cache_manager = AssetCacheManager(self._thumb_size, self, library_root=library_root)
        self._cache_manager.thumbnailReady.connect(self._on_thumb_ready)
        self._cache_manager.set_recently_removed_limit(256)

        self._state_manager = AssetListStateManager(self, self._cache_manager)
        self._row_adapter = AssetRowAdapter(self._thumb_size, self._cache_manager)

        # 2. Components Initialization
        self._filter_handler = ModelFilterHandler()
        self._transaction_manager = OptimisticTransactionManager()

        # Loader & Orchestrator
        self._data_loader = AssetDataLoader(self)  # Orchestrator manages signals
        self._orchestrator = AssetDataOrchestrator(
            self._data_loader,
            self._filter_handler,
            parent=self
        )

        # Refresh Handler
        self._refresh_handler = IncrementalUpdateHandler(
            get_current_rows=lambda: self._state_manager.rows,
            get_featured=self._get_featured_assets,
            get_filter_params=self._filter_handler.get_filter_params,
            parent=self
        )

        # Path Resolver
        self._resolver = AssetPathResolver(
            get_rows=lambda: self._state_manager.rows,
            get_row_lookup=lambda: self._state_manager.row_lookup,
            get_abs_lookup=self._state_manager.get_index_by_abs,
            get_recently_removed=self._cache_manager.recently_removed,
            album_root_getter=self.album_root
        )

        # 3. Connect Signals
        self._connect_signals()

        self._deferred_incremental_refresh: Optional[Path] = None

    def _connect_signals(self) -> None:
        """Connect internal component signals."""
        # Orchestrator
        self._orchestrator.rowsReadyForInsertion.connect(self._on_rows_ready)
        self._orchestrator.firstChunkReady.connect(self._on_first_chunk_ready)
        self._orchestrator.loadProgress.connect(self.loadProgress)
        self._orchestrator.loadFinished.connect(self._on_load_finished)
        self._orchestrator.loadError.connect(self._on_load_error)

        # Refresh Handler
        self._refresh_handler.insertRowsRequested.connect(self._on_insert_rows)
        self._refresh_handler.removeRowsRequested.connect(self._on_remove_rows)
        self._refresh_handler.rowDataChanged.connect(self._on_row_changed)
        self._refresh_handler.modelResetRequested.connect(self._on_model_reset)
        self._refresh_handler.refreshError.connect(lambda p, m: logger.error(f"Refresh error {p}: {m}"))

        # Facade
        self._facade.linksUpdated.connect(self.handle_links_updated)
        self._facade.assetUpdated.connect(self.handle_asset_updated)
        self._facade.scanChunkReady.connect(self._orchestrator._on_loader_chunk_ready) # Orchestrator handles scan chunks too

    def _get_featured_assets(self) -> List[str]:
        """Helper to get featured assets from current album."""
        if not self._facade.current_album:
            return []
        manifest = self._facade.current_album.manifest or {}
        return manifest.get("featured", []) or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_library_root(self, root: Path) -> None:
        self._cache_manager.set_library_root(root)
        self._data_loader.set_library_root(root)

    def album_root(self) -> Optional[Path]:
        return self._album_root

    def metadata_for_absolute_path(self, path: Path) -> Optional[Dict[str, object]]:
        return self._resolver.metadata_for_absolute_path(path)

    # ------------------------------------------------------------------
    # Qt Model Implementation
    # ------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        if parent is not None and parent.isValid():
            return 0
        return self._state_manager.row_count()

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        rows = self._state_manager.rows
        if not index.isValid() or not (0 <= index.row() < len(rows)):
            return None
        return self._row_adapter.data(rows[index.row()], role)

    def roleNames(self) -> Dict[int, bytes]:  # type: ignore[override]
        return role_names(super().roleNames())

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:  # type: ignore[override]
        rows = self._state_manager.rows
        if not index.isValid() or not (0 <= index.row() < len(rows)):
            return False
        if role != Roles.IS_CURRENT:
            return super().setData(index, value, role)

        normalized = bool(value)
        row = rows[index.row()]
        if bool(row.get("is_current", False)) == normalized:
            return True
        row["is_current"] = normalized
        self.dataChanged.emit(index, index, [Roles.IS_CURRENT])
        return True

    def thumbnail_loader(self) -> ThumbnailLoader:
        return self._cache_manager.thumbnail_loader()

    def get_internal_row(self, row_index: int) -> Optional[Dict[str, object]]:
        rows = self._state_manager.rows
        if not (0 <= row_index < len(rows)):
            return None
        return rows[row_index]

    def invalidate_thumbnail(self, rel: str) -> Optional[QModelIndex]:
        if not rel:
            return None
        self._cache_manager.remove_thumbnail(rel)
        loader = self._cache_manager.thumbnail_loader()
        loader.invalidate(rel)

        row_index = self._state_manager.row_lookup.get(rel)
        if row_index is None:
            return None

        model_index = self.index(row_index, 0)
        self.dataChanged.emit(model_index, model_index, [Qt.DecorationRole])
        return model_index

    # ------------------------------------------------------------------
    # Actions & Logic
    # ------------------------------------------------------------------

    def prepare_for_album(self, root: Path) -> None:
        """Reset internal state so *root* becomes the active album."""
        self._orchestrator.cancel_load()

        self._state_manager.clear_reload_pending()
        self._album_root = root
        self._orchestrator.set_album_root(root)

        self._cache_manager.reset_for_album(root)
        self._deferred_incremental_refresh = None

        self.beginResetModel()
        self._state_manager.clear_rows()
        self._transaction_manager.clear_all()
        self.endResetModel()

        self._cache_manager.clear_recently_removed()
        self._state_manager.set_virtual_reload_suppressed(False)
        self._state_manager.set_virtual_move_requires_revisit(False)

    def start_load(self) -> None:
        if not self._album_root:
            return

        self._cache_manager.clear_recently_removed()

        manifest = self._facade.current_album.manifest if self._facade.current_album else {}
        featured = manifest.get("featured", []) or []

        try:
            self._orchestrator.start_load(
                self._album_root,
                featured,
                filter_params=self._filter_handler.get_filter_params(),
                library_manager=self._facade.library_manager
            )
        except RuntimeError:
            self._state_manager.mark_reload_pending()

        self._state_manager.clear_reload_pending()

    def set_filter_mode(self, mode: Optional[str]) -> None:
        if self._filter_handler.set_mode(mode):
            self.beginResetModel()
            self._state_manager.clear_rows()
            self.endResetModel()
            self.start_load()

    def active_filter_mode(self) -> Optional[str]:
        return self._filter_handler.get_mode()

    def update_featured_status(self, rel: str, is_featured: bool) -> None:
        rel_key = str(rel)
        row_index = self._state_manager.row_lookup.get(rel_key)
        if row_index is None:
            return

        row = self._state_manager.rows[row_index]
        current = bool(row.get("featured", False))
        normalized = bool(is_featured)
        if current == normalized:
            return

        row["featured"] = normalized
        model_index = self.index(row_index, 0)
        self.dataChanged.emit(model_index, model_index, [Roles.FEATURED])

    def prioritize_rows(self, first: int, last: int) -> None:
        """Request high-priority thumbnails for the inclusive range."""
        rows = self._state_manager.rows
        if not rows:
            self._state_manager.clear_visible_rows()
            return

        if first > last:
            first, last = last, first

        first = max(first, 0)
        last = min(last, len(rows) - 1)

        requested = set(range(first, last + 1))
        self._state_manager.set_visible_rows(requested)

        for row_idx in range(first, last + 1):
            row_data = rows[row_idx]
            self._cache_manager.resolve_thumbnail(
                row_data, ThumbnailLoader.Priority.VISIBLE
            )

    # ------------------------------------------------------------------
    # Transaction Handling (Refactored)
    # ------------------------------------------------------------------

    def update_rows_for_move(
        self,
        rels: list[str],
        destination_root: Path,
        *,
        is_source_main_view: bool = False,
    ) -> None:
        if not self._album_root:
            return

        if not is_source_main_view:
            # Moving out of specific album -> Remove rows
            self._transaction_manager.register_removal(rels)
            self._handle_removal_by_rels(rels)
            self._state_manager.set_virtual_reload_suppressed(True)
        else:
            # Optimistic move (path updates)
            changed_indices = self._transaction_manager.register_move(
                rels,
                destination_root,
                self._album_root,
                self._state_manager.rows,
                self._state_manager.row_lookup,
                is_source_main_view=True
            )

            # Apply side effects (cache move)
            # Note: TransactionManager modified the rows in place.
            # We need to update cache and emit signals.
            # However, TransactionManager doesn't track "original rel" vs "new rel" for cache.
            # StateManager did this.
            # To strictly follow "new code", we should rely on TransactionManager results.
            # But we might need to manually handle cache moves if TM doesn't do it.

            # For now, we update the Qt model.
            for row_idx in changed_indices:
                model_index = self.index(row_idx, 0)
                self.dataChanged.emit(
                    model_index,
                    model_index,
                    [Roles.REL, Roles.ABS, Qt.DecorationRole],
                )

            if changed_indices:
                self._state_manager.set_virtual_reload_suppressed(True)
                self._state_manager.set_virtual_move_requires_revisit(True)

    def _handle_removal_by_rels(self, rels: List[str]) -> None:
        """Helper to remove rows and update state/cache."""
        rows_to_remove = []
        for rel in rels:
            idx = self._state_manager.row_lookup.get(rel)
            if idx is not None:
                rows_to_remove.append(idx)

        # Remove in reverse order
        for row_idx in sorted(set(rows_to_remove), reverse=True):
            if 0 <= row_idx < self._state_manager.row_count():
                row_data = self._state_manager.rows[row_idx]
                rel_key = str(row_data.get("rel", ""))
                abs_key = str(row_data.get("abs", ""))

                self.beginRemoveRows(QModelIndex(), row_idx, row_idx)
                self._state_manager.rows.pop(row_idx)
                self.endRemoveRows()

                if rel_key:
                    self._state_manager.row_lookup.pop(rel_key, None)
                    self._cache_manager.remove_thumbnail(rel_key)
                    self._cache_manager.remove_placeholder(rel_key)
                if abs_key:
                    self._cache_manager.stash_recently_removed(abs_key, row_data)

        self._state_manager.rebuild_lookup()

    def finalise_move_results(self, moves: List[Tuple[Path, Path]]) -> None:
        """Reconcile optimistic move updates."""
        # Delegate to TransactionManager
        updated_rows = self._transaction_manager.finalize_moves(
            moves,
            self._state_manager.rows,
            self._state_manager.row_lookup,
            self._album_root
        )

        for row_idx in updated_rows:
            model_index = self.index(row_idx, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [Roles.REL, Roles.ABS, Qt.DecorationRole],
            )

        # Handle removals if any were pending?
        # TransactionManager stores pending removals but doesn't do anything with them in finalize_moves.
        # We assume they are already removed from the model in update_rows_for_move.
        self._transaction_manager.clear_pending_removals()

    def rollback_pending_moves(self) -> None:
        restored_rows = self._transaction_manager.rollback_moves(
            self._state_manager.rows,
            self._state_manager.row_lookup,
            self._album_root
        )

        for row_idx in restored_rows:
            model_index = self.index(row_idx, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [Roles.REL, Roles.ABS, Qt.DecorationRole],
            )

        self._state_manager.set_virtual_reload_suppressed(False)
        self._state_manager.set_virtual_move_requires_revisit(False)

    def has_pending_move_placeholders(self) -> bool:
        return (
            self._transaction_manager.has_pending_moves() or
            self._transaction_manager.has_pending_removals()
        )

    def populate_from_cache(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Handlers for Signals (Orchestrator/Refresh)
    # ------------------------------------------------------------------

    @Slot(list, bool)
    def _on_first_chunk_ready(self, chunk: list, should_reset: bool):
        if should_reset:
            self.beginResetModel()
            self._state_manager.clear_rows()
            self._state_manager.append_chunk(chunk)
            self.endResetModel()
            self.prioritize_rows(0, len(chunk) - 1)
        else:
            self._on_rows_ready(-1, chunk)

    @Slot(int, list)
    def _on_rows_ready(self, start_row: int, rows: list):
        if not rows:
            return

        current_count = self._state_manager.row_count()
        if start_row < 0:
            start_row = current_count

        self.beginInsertRows(QModelIndex(), start_row, start_row + len(rows) - 1)
        self._state_manager.append_chunk(rows)
        self.endInsertRows()

        self._state_manager.on_external_row_inserted(start_row, len(rows))

    @Slot(Path, bool)
    def _on_load_finished(self, root: Path, success: bool):
        self.loadFinished.emit(root, success)

        # Check deferred refresh
        if (
            success and
            self._album_root and
            self._deferred_incremental_refresh == normalise_for_compare(self._album_root)
        ):
            self._deferred_incremental_refresh = None
            self._refresh_handler.refresh_from_index(self._album_root)

        should_restart = self._state_manager.consume_pending_reload(self._album_root, root)
        if should_restart:
            QTimer.singleShot(0, self.start_load)

    @Slot(Path, str)
    def _on_load_error(self, root: Path, message: str):
        self._facade.errorRaised.emit(message)
        self.loadFinished.emit(root, False)
        should_restart = self._state_manager.consume_pending_reload(self._album_root, root)
        if should_restart:
            QTimer.singleShot(0, self.start_load)

    @Slot(int, list)
    def _on_insert_rows(self, index: int, rows: list):
        self.beginInsertRows(QModelIndex(), index, index + len(rows) - 1)
        # We need to insert into state manager at specific index
        # StateManager only has append_chunk. We might need to manipulate _rows directly.
        # But we must update lookups!
        # AssetListStateManager does NOT have insert_rows method, only append.
        # But RefreshHandler calculates index.

        # Manually insert
        current_rows = self._state_manager.rows
        for i, row in enumerate(rows):
            current_rows.insert(index + i, row)

        self.endInsertRows()
        self._state_manager.rebuild_lookup()
        self._state_manager.on_external_row_inserted(index, len(rows))

        # Cache updates
        for row in rows:
            rel = row.get("rel")
            if rel:
                self._cache_manager.remove_thumbnail(rel)

    @Slot(int, int)
    def _on_remove_rows(self, index: int, count: int):
        self.beginRemoveRows(QModelIndex(), index, index + count - 1)
        # Remove from state
        rows = self._state_manager.rows
        for _ in range(count):
            if index < len(rows):
                row = rows.pop(index)
                # Cleanup cache
                rel = row.get("rel")
                if rel:
                    self._cache_manager.remove_thumbnail(rel)
        self.endRemoveRows()
        self._state_manager.rebuild_lookup()
        self._state_manager.on_external_row_removed(index, None)

    @Slot(int, dict)
    def _on_row_changed(self, index: int, row_data: dict):
        if index < 0:
            # Lookup index
            rel = row_data.get("rel")
            if rel:
                index = self._state_manager.row_lookup.get(rel, -1)

        if index < 0 or index >= self._state_manager.row_count():
            return

        self._state_manager.update_row_at_index(index, row_data)

        model_index = self.index(index, 0)
        self.dataChanged.emit(
            model_index,
            model_index,
            [Roles.REL, Roles.ABS, Roles.SIZE, Qt.DecorationRole]
        )
        self.invalidate_thumbnail(str(row_data.get("rel", "")))

    @Slot(list)
    def _on_model_reset(self, new_rows: list):
        self.beginResetModel()
        self._state_manager.set_rows(new_rows)
        self.endResetModel()
        self._cache_manager.reset_caches_for_new_rows(new_rows)
        self.prioritize_rows(0, len(new_rows)-1)

    @Slot(Path, str, QPixmap)
    def _on_thumb_ready(self, root: Path, rel: str, pixmap: QPixmap) -> None:
        if not self._album_root or root != self._album_root:
            return
        index = self._state_manager.row_lookup.get(rel)
        if index is None:
            return
        model_index = self.index(index, 0)
        self.dataChanged.emit(model_index, model_index, [Qt.DecorationRole])

    @Slot(Path)
    def handle_asset_updated(self, path: Path) -> None:
        metadata = self.metadata_for_absolute_path(path)
        if metadata:
            self.invalidate_thumbnail(str(metadata.get("rel", "")))

    @Slot(Path)
    def handle_links_updated(self, root: Path) -> None:
        if not self._album_root:
            return

        if self._state_manager.suppress_virtual_reload():
            if self._state_manager.virtual_move_requires_revisit():
                return
            self._state_manager.set_virtual_reload_suppressed(False)

        updated_root = normalise_for_compare(Path(root))
        album_root = normalise_for_compare(self._album_root)

        if not is_descendant_path(updated_root, album_root) and updated_root != album_root:
            return

        descendant_root = updated_root if updated_root != album_root else None

        if self._orchestrator.is_loading():
            self._deferred_incremental_refresh = self._album_root
            return

        self._refresh_handler.refresh_from_index(self._album_root, descendant_root)
