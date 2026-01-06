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

from ...tasks.thumbnail_loader import ThumbnailLoader
from ..asset_cache_manager import AssetCacheManager
from ..asset_state_manager import AssetListStateManager
from ..asset_row_adapter import AssetRowAdapter
from ..list_diff_calculator import ListDiffCalculator
from ..roles import Roles, role_names
from .controller import AssetListController
from .....utils.pathutils import (
    normalise_for_compare,
    normalise_rel_value,
)

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from ....facade import AppFacade


logger = logging.getLogger(__name__)


class AssetListModel(QAbstractListModel):
    """Expose album assets to Qt views."""

    # ``Path`` is used explicitly so that static compilers such as Nuitka can
    # prove that the connected slots accept the same signature.
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)

    def __init__(self, facade: "AppFacade", parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._facade = facade
        self._album_root: Optional[Path] = None
        self._thumb_size = QSize(512, 512)
        # Track whether this model is in background cache mode (not actively displayed
        # but data is kept warm for instant switching).
        self._is_background_cache: bool = False

        # Try to acquire library root early if available
        library_root = None
        if self._facade.library_manager:
            library_root = self._facade.library_manager.root()

        self._cache_manager = AssetCacheManager(
            self._thumb_size, self, library_root=library_root
        )
        self._cache_manager.thumbnailReady.connect(self._on_thumb_ready)

        self._state_manager = AssetListStateManager(self, self._cache_manager)
        self._cache_manager.set_recently_removed_limit(256)

        self._row_adapter = AssetRowAdapter(self._thumb_size, self._cache_manager)

        # Initialize Controller
        self._controller = AssetListController(
            facade,
            duplication_checker=self._check_duplication,
            parent=self,
        )
        self._controller.batchReady.connect(self._on_batch_ready)
        self._controller.incrementalReady.connect(self._apply_incremental_results)
        self._controller.loadProgress.connect(self.loadProgress)
        self._controller.loadFinished.connect(self._on_controller_load_finished)
        self._controller.error.connect(self._on_controller_error)

        self._facade.linksUpdated.connect(self.handle_links_updated)
        self._facade.assetUpdated.connect(self.handle_asset_updated)

    def _check_duplication(self, rel: str, abs_key: Optional[str]) -> bool:
        """Callback for Controller to check if an item exists in the model."""
        if rel in self._state_manager.row_lookup:
            return True
        if abs_key and self._state_manager.get_index_by_abs(abs_key) is not None:
            return True
        return False

    def set_library_root(self, root: Path) -> None:
        """Update the centralized library root for thumbnail generation and index access."""
        self._cache_manager.set_library_root(root)
        self._controller.set_library_root(root)

    def album_root(self) -> Optional[Path]:
        """Return the path of the currently open album, if any."""
        return self._album_root

    def is_valid(self) -> bool:
        """Return ``True`` if the model is populated and tied to a valid root."""
        return self._album_root is not None

    def mark_as_background_cache(self) -> None:
        """Mark the model as being in background cache mode.
        
        When the model is marked as a background cache, its data is kept warm
        in memory but it's not currently displayed. This allows instant
        switching back to the model without reloading from the index.
        """
        self._is_background_cache = True
        logger.debug(
            "Model marked as background cache: %s (%d rows)",
            self._album_root,
            self.rowCount(),
        )

    def is_background_cache(self) -> bool:
        """Return ``True`` if the model is currently in background cache mode."""
        return self._is_background_cache

    def clear_background_cache_state(self) -> None:
        """Clear the background cache flag when the model becomes active."""
        if self._is_background_cache:
            self._is_background_cache = False
            logger.debug(
                "Model activated from background cache: %s (%d rows)",
                self._album_root,
                self.rowCount(),
            )

    def metadata_for_absolute_path(self, path: Path) -> Optional[Dict[str, object]]:
        """Return the cached metadata row for *path* if it belongs to the model."""
        rows = self._state_manager.rows
        if not rows:
            return None

        album_root = self._album_root
        try:
            normalized_path = path.resolve()
        except OSError:
            normalized_path = path

        if album_root is not None:
            try:
                normalized_root = album_root.resolve()
            except OSError:
                normalized_root = album_root
            try:
                rel_key = normalized_path.relative_to(normalized_root).as_posix()
            except ValueError:
                rel_key = None
            else:
                row_index = self._state_manager.row_lookup.get(rel_key)
                if row_index is not None and 0 <= row_index < len(rows):
                    return rows[row_index]

        normalized_str = str(normalized_path)

        # O(1) Lookup optimization
        row_index = self._state_manager.get_index_by_abs(normalized_str)
        if row_index is not None and 0 <= row_index < len(rows):
            return rows[row_index]

        cached = self._cache_manager.recently_removed(normalized_str)
        if cached is not None:
            return cached
        return None

    def remove_rows(self, indexes: list[QModelIndex]) -> None:
        """Remove assets referenced by *indexes*, tolerating proxy selections."""
        self._state_manager.remove_rows(indexes)

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

        changed_rows = self._state_manager.update_rows_for_move(
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
        updated_rows = self._state_manager.finalise_move_results(
            moves, self._album_root
        )
        for row in updated_rows:
            model_index = self.index(row, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [Roles.REL, Roles.ABS, Qt.DecorationRole],
            )

    def rollback_pending_moves(self) -> None:
        """Restore original metadata for moves that failed or were cancelled."""
        restored_rows = self._state_manager.rollback_pending_moves(self._album_root)
        for row in restored_rows:
            model_index = self.index(row, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [Roles.REL, Roles.ABS, Qt.DecorationRole],
            )

    def has_pending_move_placeholders(self) -> bool:
        """Return ``True`` when optimistic move updates are awaiting results."""
        return self._state_manager.has_pending_move_placeholders()

    def populate_from_cache(self) -> bool:
        """Synchronously load cached index data when the file is small.

        Disabled to enforce streaming behavior and prevent main thread blocking on large albums.
        """
        return False

    # ------------------------------------------------------------------
    # Qt model implementation
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        if parent is not None and parent.isValid():  # pragma: no cover - tree fallback
            return 0
        return self._state_manager.row_count()

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        rows = self._state_manager.rows
        if not index.isValid() or not (0 <= index.row() < len(rows)):
            return None
        return self._row_adapter.data(rows[index.row()], role)

    def roleNames(self) -> Dict[int, bytes]:  # type: ignore[override]
        return role_names(super().roleNames())

    def setData(
        self, index: QModelIndex, value: Any, role: int = Qt.EditRole
    ) -> bool:  # type: ignore[override]
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
        """Return the raw dictionary for *row_index* to bypass the Qt role API."""
        rows = self._state_manager.rows
        if not (0 <= row_index < len(rows)):
            return None
        return rows[row_index]

    def invalidate_thumbnail(self, rel: str) -> Optional[QModelIndex]:
        """Remove cached thumbnails and notify views for *rel*."""
        if not rel:
            return None
        self._cache_manager.remove_thumbnail(rel)
        loader = self._cache_manager.thumbnail_loader()
        loader.invalidate(rel)
        row_index = self._state_manager.row_lookup.get(rel)
        rows = self._state_manager.rows
        if row_index is None or not (0 <= row_index < len(rows)):
            return None
        model_index = self.index(row_index, 0)
        self.dataChanged.emit(model_index, model_index, [Qt.DecorationRole])
        return model_index

    # ------------------------------------------------------------------
    # Pagination support (Qt canFetchMore/fetchMore API)
    # ------------------------------------------------------------------
    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        """Return True if more data can be loaded via pagination.
        
        This method is part of the Qt model API and is called automatically
        by views when they reach the end of the currently loaded data.
        """
        if parent.isValid():
            return False
        return self._controller.can_load_more()

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        """Load the next page of data using cursor-based pagination.
        
        This method is part of the Qt model API and is called automatically
        by views when canFetchMore() returns True and the view needs more data.
        """
        if parent.isValid():
            return
        self._controller.load_next_page()

    def load_next_page(self) -> bool:
        """Explicitly request the next page of data.
        
        Returns True if a page load was started, False otherwise.
        This method is intended for use by the view or controller when
        the user scrolls near the end of the current data.
        """
        return self._controller.load_next_page()

    def all_data_loaded(self) -> bool:
        """Return True if all data has been loaded (no more pages)."""
        return self._controller.all_data_loaded()

    # ------------------------------------------------------------------
    # Facade callbacks
    # ------------------------------------------------------------------
    def prepare_for_album(self, root: Path) -> None:
        """Reset internal state so *root* becomes the active album."""
        # Let the controller handle any pending reload state before we
        # mutate our own album root and clear the state manager flags.
        self._controller.prepare_for_album(root)

        self._album_root = root
        self._state_manager.clear_reload_pending()
        self._cache_manager.reset_for_album(root)

        self.beginResetModel()
        self._state_manager.clear_rows()
        self.endResetModel()

        self._cache_manager.clear_recently_removed()
        self._state_manager.set_virtual_reload_suppressed(False)
        self._state_manager.set_virtual_move_requires_revisit(False)

    def update_featured_status(self, rel: str, is_featured: bool) -> None:
        """Update the cached ``featured`` flag for the asset identified by *rel*."""
        rel_key = str(rel)
        row_index = self._state_manager.row_lookup.get(rel_key)
        rows = self._state_manager.rows
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

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def set_filter_mode(self, mode: Optional[str]) -> None:
        """Apply a new filter mode and trigger a reload if necessary."""
        # Note: We clear model here to match original behavior of immediate clearing
        # before the async load kicks in.
        normalized = mode.casefold() if isinstance(mode, str) and mode else None
        if normalized == self._controller.active_filter_mode():
            return

        self.beginResetModel()
        self._state_manager.clear_rows()
        self.endResetModel()

        self._controller.set_filter_mode(mode)

    def active_filter_mode(self) -> Optional[str]:
        return self._controller.active_filter_mode()

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------
    def start_load(self) -> None:
        """Start loading data."""
        self._state_manager.clear_reload_pending()
        self._cache_manager.clear_recently_removed()
        self._controller.start_load()

    def _on_batch_ready(self, chunk: List[Dict[str, object]], is_reset: bool) -> None:
        """Handle incoming data batch from Controller."""
        if not chunk:
            return

        if is_reset:
            # If the controller signals a reset (e.g. first chunk), we reset the model.
            self.beginResetModel()
            self._state_manager.clear_rows()
            self._state_manager.append_chunk(chunk)
            self.endResetModel()
            self.prioritize_rows(0, len(chunk) - 1)
        else:
            # Append mode
            start_row = self._state_manager.row_count()
            end_row = start_row + len(chunk) - 1

            self.beginInsertRows(QModelIndex(), start_row, end_row)
            self._state_manager.append_chunk(chunk)
            self.endInsertRows()

            self._state_manager.on_external_row_inserted(start_row, len(chunk))

    def _on_controller_load_finished(self, root: Path, success: bool) -> None:
        """Handle load completion."""
        # Check for pending reload in state manager
        should_restart = self._state_manager.consume_pending_reload(
            self._album_root, root
        )

        self.loadFinished.emit(root, success)

        if should_restart:
            QTimer.singleShot(0, self.start_load)

    def _on_controller_error(self, root: Path, message: str) -> None:
        """Handle load error."""
        self._facade.errorRaised.emit(message)
        # loadFinished is emitted by controller, so we don't need to double emit?
        # Controller emits loadFinished AFTER error.
        # But we also need to check pending reload.
        # Controller calls loadFinished(root, False) internally.
        # So _on_controller_load_finished will be called.
        # We just handle the Facade notification here.

    # ------------------------------------------------------------------
    # Thumbnail helpers
    # ------------------------------------------------------------------
    def prioritize_rows(self, first: int, last: int) -> None:
        """Request high-priority thumbnails for the inclusive range *first*→*last*."""
        rows = self._state_manager.rows
        if not rows:
            self._state_manager.clear_visible_rows()
            return

        if first > last:
            first, last = last, first

        first = max(first, 0)
        last = min(last, len(rows) - 1)
        if first > last:
            self._state_manager.clear_visible_rows()
            return

        requested = set(range(first, last + 1))
        if not requested:
            self._state_manager.clear_visible_rows()
            return

        uncached = {
            row
            for row in requested
            if self._cache_manager.thumbnail_for(str(rows[row]["rel"])) is None
        }
        if not uncached:
            self._state_manager.set_visible_rows(requested)
            return
        if uncached.issubset(self._state_manager.visible_rows):
            self._state_manager.set_visible_rows(requested)
            return

        self._state_manager.set_visible_rows(requested)
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
        index = self._state_manager.row_lookup.get(rel)
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

    @Slot(Path)
    def handle_links_updated(self, root: Path) -> None:
        """React to :mod:`links.json` refreshes triggered by the backend."""
        if not self._album_root:
            logger.debug(
                "AssetListModel: linksUpdated ignored because no album root is active."
            )
            return

        # Check suppression logic first
        updated_root = normalise_for_compare(Path(root))
        if self._state_manager.suppress_virtual_reload():
            if self._state_manager.virtual_move_requires_revisit():
                logger.debug(
                    "AssetListModel: holding reload for %s until the aggregate view is reopened.",
                    updated_root,
                )
                return

            logger.debug(
                "AssetListModel: finishing temporary suppression for %s after non-aggregate move.",
                updated_root,
            )
            self._state_manager.set_virtual_reload_suppressed(False)
            if self._state_manager.rows:
                # Check if update targets our view and trigger refresh if applicable
                handled = self._controller.handle_links_updated(root, self._album_root)
                if not handled:
                    logger.debug(
                        "AssetListModel: suppression cleared but update doesn't target current view."
                    )
            return

        # Delegate to controller
        handled = self._controller.handle_links_updated(root, self._album_root)
        if not handled:
            logger.debug(
                "AssetListModel: linksUpdated ignored because update doesn't target current view."
            )

    def _apply_incremental_results(
        self, fresh_rows: List[Dict[str, object]], root: Path
    ) -> None:
        """Apply the fetched rows to the model via diffing."""
        if not self._album_root or root != self._album_root:
            return

        if self._apply_incremental_rows(fresh_rows):
            logger.debug(
                "AssetListModel: applied incremental refresh for %s (%d rows).",
                root,
                len(fresh_rows),
            )

    def _apply_incremental_rows(self, new_rows: List[Dict[str, object]]) -> bool:
        """Merge *new_rows* into the model without clearing the entire view."""
        current_rows = self._state_manager.rows

        diff = ListDiffCalculator.calculate_diff(current_rows, new_rows)

        if diff.is_reset:
            self.beginResetModel()
            self._state_manager.set_rows(new_rows)
            self.endResetModel()
            self._cache_manager.reset_caches_for_new_rows(new_rows)
            self._state_manager.clear_visible_rows()
            return True

        if diff.is_empty_to_empty:
            return False

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
            self._state_manager.on_external_row_removed(index, rel_key)
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
            self._state_manager.on_external_row_inserted(position)
            if rel_key:
                self._cache_manager.remove_thumbnail(rel_key)
                self._cache_manager.remove_placeholder(rel_key)
            abs_value = row_data.get("abs")
            if abs_value:
                self._cache_manager.remove_recently_removed(str(abs_value))

        # Apply updates
        if diff.structure_changed:
            self._state_manager.clear_visible_rows()

        self._state_manager.rebuild_lookup()

        if diff.structure_changed:
            self._cache_manager.reset_caches_for_new_rows(current_rows)

        # Update data for changed rows
        for replacement in diff.changed_items:
            rel_key = normalise_rel_value(replacement.get("rel"))
            if not rel_key:
                continue

            row_index = self._state_manager.row_lookup.get(rel_key)
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

        return diff.structure_changed or bool(diff.changed_items)

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
