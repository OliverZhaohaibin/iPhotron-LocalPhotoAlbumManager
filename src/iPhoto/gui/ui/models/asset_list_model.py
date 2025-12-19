"""List model combining ``index.jsonl`` and ``links.json`` data."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSize,
    Qt,
    Signal,
    Slot,
    QTimer,
)
from PySide6.QtGui import QPixmap

from ..tasks.thumbnail_loader import ThumbnailLoader
from .asset_cache_manager import AssetCacheManager
from .asset_row_adapter import AssetRowAdapter
from .roles import Roles, role_names
from .logic.data_repository import AssetRepository
from .logic.ingestion_controller import AssetIngestionController
from .logic.live_sync_manager import AssetLiveSyncManager
from ....utils.pathutils import normalise_rel_value

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from ...facade import AppFacade


logger = logging.getLogger(__name__)


class AssetListModel(QAbstractListModel):
    """Expose album assets to Qt views."""

    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)

    def __init__(self, facade: "AppFacade", parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._facade = facade
        self._album_root: Optional[Path] = None
        self._thumb_size = QSize(512, 512)
        self._active_filter: Optional[str] = None

        # Try to acquire library root early if available
        library_root = None
        if self._facade.library_manager:
            library_root = self._facade.library_manager.root()

        self._cache_manager = AssetCacheManager(self._thumb_size, self, library_root=library_root)
        self._cache_manager.thumbnailReady.connect(self._on_thumb_ready)

        self._repo = AssetRepository(self, self._cache_manager)

        self._ingestion = AssetIngestionController(self, self._repo, facade)
        self._ingestion.batchReady.connect(self._on_ingestion_batch_ready)
        self._ingestion.loadProgress.connect(self.loadProgress)
        self._ingestion.loadFinished.connect(self._on_load_finished)
        self._ingestion.error.connect(self._on_ingestion_error)

        self._sync = AssetLiveSyncManager(self, self._repo, facade, self._ingestion.is_loading)
        self._sync.diffReady.connect(self._on_diff_ready)
        self._sync.error.connect(lambda msg: self._facade.errorRaised.emit(msg))

        # AssetRowAdapter helps with data() calls
        self._row_adapter = AssetRowAdapter(self._thumb_size, self._cache_manager)

        self._facade.assetUpdated.connect(self.handle_asset_updated)

    def set_library_root(self, root: Path) -> None:
        """Update the centralized library root for thumbnail generation and index access."""
        self._cache_manager.set_library_root(root)
        self._ingestion.set_library_root(root)

    def album_root(self) -> Optional[Path]:
        """Return the path of the currently open album, if any."""
        return self._album_root

    def metadata_for_absolute_path(self, path: Path) -> Optional[Dict[str, object]]:
        """Return the cached metadata row for *path* if it belongs to the model."""
        return self._repo.metadata_for_absolute_path(path, self._album_root)

    def remove_rows(self, indexes: list[QModelIndex]) -> None:
        """Remove assets referenced by *indexes*, tolerating proxy selections."""
        self._repo.remove_rows(indexes)

    def update_rows_for_move(
        self,
        rels: list[str],
        destination_root: Path,
        *,
        is_source_main_view: bool = False,
    ) -> None:
        """Apply optimistic UI updates when a move operation is queued."""
        if not self._album_root:
            return

        changed_rows = self._repo.update_rows_for_move(
            rels,
            destination_root,
            self._album_root,
            is_source_main_view=is_source_main_view,
        )

        for row in changed_rows:
            model_index = self.index(row, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [Roles.REL, Roles.ABS, Qt.DecorationRole],
            )

    def finalise_move_results(self, moves: List[Tuple[Path, Path]]) -> None:
        """Reconcile optimistic move updates with the worker results."""
        updated_rows = self._repo.finalise_move_results(moves, self._album_root)

        for row in updated_rows:
            model_index = self.index(row, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [Roles.REL, Roles.ABS, Qt.DecorationRole],
            )

    def rollback_pending_moves(self) -> None:
        """Restore original metadata for moves that failed or were cancelled."""
        restored_rows = self._repo.rollback_pending_moves(self._album_root)

        for row in restored_rows:
            model_index = self.index(row, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [Roles.REL, Roles.ABS, Qt.DecorationRole],
            )

    def has_pending_move_placeholders(self) -> bool:
        """Return ``True`` when optimistic move updates are awaiting results."""
        return self._repo.has_pending_move_placeholders()

    def populate_from_cache(self) -> bool:
        """Synchronously load cached index data (Disabled)."""
        return False

    # ------------------------------------------------------------------
    # Qt model implementation
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        if parent is not None and parent.isValid():
            return 0
        return self._repo.row_count()

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        rows = self._repo.rows
        if not index.isValid() or not (0 <= index.row() < len(rows)):
            return None
        return self._row_adapter.data(rows[index.row()], role)

    def roleNames(self) -> Dict[int, bytes]:  # type: ignore[override]
        return role_names(super().roleNames())

    def setData(
        self, index: QModelIndex, value: Any, role: int = Qt.EditRole
    ) -> bool:  # type: ignore[override]
        rows = self._repo.rows
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
        """Return the raw dictionary for *row_index* to bypass the Qt role API."""
        return self._repo.get_by_index(row_index)

    def invalidate_thumbnail(self, rel: str) -> Optional[QModelIndex]:
        """Remove cached thumbnails and notify views for *rel*."""
        if not rel:
            return None
        self._cache_manager.remove_thumbnail(rel)
        loader = self._cache_manager.thumbnail_loader()
        loader.invalidate(rel)
        row_index = self._repo.row_lookup.get(rel)
        if row_index is None or not (0 <= row_index < self._repo.row_count()):
            return None
        model_index = self.index(row_index, 0)
        self.dataChanged.emit(model_index, model_index, [Qt.DecorationRole])
        return model_index

    # ------------------------------------------------------------------
    # Facade callbacks & Setup
    # ------------------------------------------------------------------
    def prepare_for_album(self, root: Path) -> None:
        """Reset internal state so *root* becomes the active album."""
        self._ingestion.reset(root)
        self._sync.set_album_root(root)

        self._album_root = root
        self._cache_manager.reset_for_album(root)

        self.beginResetModel()
        self._repo.clear_rows()
        self.endResetModel()

        self._cache_manager.clear_recently_removed()
        self._repo.set_virtual_reload_suppressed(False)
        self._repo.set_virtual_move_requires_revisit(False)

    def update_featured_status(self, rel: str, is_featured: bool) -> None:
        """Update the cached ``featured`` flag for the asset identified by *rel*."""
        rel_key = str(rel)
        row_index = self._repo.row_lookup.get(rel_key)
        rows = self._repo.rows
        if row_index is None or not (0 <= row_index < len(rows)):
            return

        row = rows[row_index]
        current = bool(row.get("featured", False))
        normalized = bool(is_featured)
        if current == normalized:
            return

        row["featured"] = normalized
        model_index = self.index(row_index, 0)
        self.dataChanged.emit(model_index, model_index, [Roles.FEATURED])

    def set_filter_mode(self, mode: Optional[str]) -> None:
        """Apply a new filter mode and trigger a reload."""
        normalized = mode.casefold() if isinstance(mode, str) and mode else None
        if normalized == self._active_filter:
            return

        self._active_filter = normalized
        self._ingestion.set_active_filter(normalized)
        self._sync.set_active_filter(normalized)

        self.beginResetModel()
        self._repo.clear_rows()
        self.endResetModel()

        self.start_load()

    def active_filter_mode(self) -> Optional[str]:
        return self._active_filter

    def start_load(self) -> None:
        if not self._album_root:
            return
        self._cache_manager.clear_recently_removed()
        self._ingestion.start_load(self._album_root)

    # ------------------------------------------------------------------
    # Handlers for Ingestion & Sync
    # ------------------------------------------------------------------
    def _on_ingestion_batch_ready(self, batch: List[Dict[str, object]]) -> None:
        """Handle a batch of loaded rows."""
        start_row = self._repo.row_count()
        end_row = start_row + len(batch) - 1

        self.beginInsertRows(QModelIndex(), start_row, end_row)
        self._repo.append_chunk(batch)
        self.endInsertRows()

        self._repo.on_external_row_inserted(start_row, len(batch))

        # Prioritize thumbnails for the newly inserted batch, assuming they might be visible.
        # This mirrors the optimization in the original implementation.
        self.prioritize_rows(start_row, end_row)

    def _on_load_finished(self, root: Path, success: bool) -> None:
        """Handle load finished event."""
        self.loadFinished.emit(root, success)
        if success:
             self._sync.process_deferred_refresh()

    def _on_ingestion_error(self, message: str) -> None:
        self._facade.errorRaised.emit(message)

    def _on_diff_ready(self, diff: Any, fresh_rows: List[Dict[str, object]]) -> None:
        """Apply diff calculated by LiveSyncManager."""

        if diff.is_reset:
            self.beginResetModel()
            self._repo.set_rows(fresh_rows)
            self.endResetModel()
            self._cache_manager.reset_caches_for_new_rows(fresh_rows)
            self._repo.clear_visible_rows()
            logger.debug("AssetListModel: Applied reset diff (%d rows).", len(fresh_rows))
            return

        if diff.is_empty_to_empty:
            return

        current_rows = self._repo.rows

        # Apply removals
        for index in diff.removed_indices:
            if not (0 <= index < len(current_rows)):
                continue
            row_snapshot = current_rows[index]
            rel_key = normalise_rel_value(row_snapshot.get("rel"))
            abs_key = row_snapshot.get("abs")

            self.beginRemoveRows(QModelIndex(), index, index)
            current_rows.pop(index)
            self.endRemoveRows()

            self._repo.on_external_row_removed(index, rel_key)
            if rel_key:
                self._cache_manager.remove_thumbnail(rel_key)
                self._cache_manager.remove_placeholder(rel_key)
            if abs_key:
                self._cache_manager.remove_recently_removed(str(abs_key))

        # Apply insertions
        for insert_index, row_data, rel_key in diff.inserted_items:
            position = max(0, min(insert_index, len(current_rows)))

            self.beginInsertRows(QModelIndex(), position, position)
            current_rows.insert(position, row_data)
            self.endInsertRows()

            self._repo.on_external_row_inserted(position)
            if rel_key:
                self._cache_manager.remove_thumbnail(rel_key)
                self._cache_manager.remove_placeholder(rel_key)
            abs_value = row_data.get("abs")
            if abs_value:
                self._cache_manager.remove_recently_removed(str(abs_value))

        # Apply updates
        if diff.structure_changed:
            self._repo.clear_visible_rows()

        self._repo.rebuild_lookup()

        if diff.structure_changed:
            self._cache_manager.reset_caches_for_new_rows(current_rows)

        # Update data for changed rows
        for replacement in diff.changed_items:
            rel_key = normalise_rel_value(replacement.get("rel"))
            if not rel_key:
                continue

            row_index = self._repo.row_lookup.get(rel_key)
            if row_index is None or not (0 <= row_index < len(current_rows)):
                continue

            original = current_rows[row_index]
            current_rows[row_index] = replacement

            model_index = self.index(row_index, 0)
            affected_roles = [
                Roles.REL,
                Roles.ABS,
                Roles.SIZE,
                Roles.DT,
                Roles.IS_IMAGE,
                Roles.IS_VIDEO,
                Roles.IS_LIVE,
                Qt.DecorationRole,
            ]
            self.dataChanged.emit(model_index, model_index, affected_roles)

            if self._should_invalidate_thumbnail(original, replacement):
                self.invalidate_thumbnail(rel_key)

        if diff.structure_changed or diff.changed_items:
            logger.debug("AssetListModel: Applied diff (removed %d, inserted %d, updated %d).",
                         len(diff.removed_indices), len(diff.inserted_items), len(diff.changed_items))

    def _should_invalidate_thumbnail(
        self, old_row: Dict[str, object], new_row: Dict[str, object]
    ) -> bool:
        """Return True if the thumbnail must be regenerated based on row changes."""
        visual_keys = {
            "ts",
            "bytes",
            "abs",
            "w",
            "h",
            "still_image_time",
        }
        for key in visual_keys:
            if old_row.get(key) != new_row.get(key):
                return True
        return False

    # ------------------------------------------------------------------
    # Thumbnail helpers
    # ------------------------------------------------------------------
    def prioritize_rows(self, first: int, last: int) -> None:
        """Request high-priority thumbnails for the inclusive range *first*→*last*."""
        rows = self._repo.rows
        if not rows:
            self._repo.clear_visible_rows()
            return

        if first > last:
            first, last = last, first

        first = max(first, 0)
        last = min(last, len(rows) - 1)
        if first > last:
            self._repo.clear_visible_rows()
            return

        requested = set(range(first, last + 1))
        if not requested:
            self._repo.clear_visible_rows()
            return

        uncached = {
            row
            for row in requested
            if self._cache_manager.thumbnail_for(str(rows[row]["rel"])) is None
        }
        if not uncached:
            self._repo.set_visible_rows(requested)
            return
        if uncached.issubset(self._repo.visible_rows):
            self._repo.set_visible_rows(requested)
            return

        self._repo.set_visible_rows(requested)
        for row in range(first, last + 1):
            if row not in uncached:
                continue
            row_data = rows[row]
            self._cache_manager.resolve_thumbnail(
                row_data, ThumbnailLoader.Priority.VISIBLE
            )

    def _on_thumb_ready(self, root: Path, rel: str, pixmap: QPixmap) -> None:
        if not self._album_root or root != self._album_root:
            return
        index = self._repo.row_lookup.get(rel)
        if index is None:
            return
        model_index = self.index(index, 0)
        self.dataChanged.emit(model_index, model_index, [Qt.DecorationRole])

    @Slot(Path)
    def handle_asset_updated(self, path: Path) -> None:
        """Refresh the thumbnail and view when an asset is modified."""
        metadata = self.metadata_for_absolute_path(path)
        if metadata is None:
            return

        rel = metadata.get("rel")
        if not rel:
            return

        self.invalidate_thumbnail(str(rel))
