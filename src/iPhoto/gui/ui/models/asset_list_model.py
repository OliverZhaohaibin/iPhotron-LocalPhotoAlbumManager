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
from ..tasks.asset_loader_worker import (
    build_asset_entry,
    normalize_featured,
)
from .asset_cache_manager import AssetCacheManager
from .asset_data_loader import AssetDataLoader
from .asset_state_manager import AssetListStateManager
from .asset_row_adapter import AssetRowAdapter
from .list_diff_calculator import ListDiffCalculator
from .roles import Roles, role_names
from ....models.album import Album
from ....errors import IPhotoError
from ....utils.pathutils import (
    normalise_for_compare,
    is_descendant_path,
    normalise_rel_value,
)

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from ...facade import AppFacade


logger = logging.getLogger(__name__)


class AssetListModel(QAbstractListModel):
    """Expose album assets to Qt views."""

    # ``Path`` is used explicitly so that static compilers such as Nuitka can
    # prove that the connected slots accept the same signature.  Relying on the
    # generic ``object`` type confuses Nuitka's patched ``Signal.connect``
    # implementation and results in runtime errors during packaging.
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)

    # Tuning constants for streaming updates
    _STREAM_FLUSH_INTERVAL_MS = 100
    _STREAM_FLUSH_THRESHOLD = 500

    def __init__(self, facade: "AppFacade", parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._facade = facade
        self._album_root: Optional[Path] = None
        self._thumb_size = QSize(512, 512)
        self._cache_manager = AssetCacheManager(self._thumb_size, self)
        self._cache_manager.thumbnailReady.connect(self._on_thumb_ready)
        self._data_loader = AssetDataLoader(self)
        self._data_loader.chunkReady.connect(self._on_loader_chunk_ready)
        self._data_loader.loadProgress.connect(self._on_loader_progress)
        self._data_loader.loadFinished.connect(self._on_loader_finished)
        self._data_loader.error.connect(self._on_loader_error)
        self._state_manager = AssetListStateManager(self, self._cache_manager)
        self._cache_manager.set_recently_removed_limit(256)

        # AssetDataAccumulator is removed in favor of direct streaming buffers
        self._row_adapter = AssetRowAdapter(self._thumb_size, self._cache_manager)

        # Streaming buffer state
        self._pending_chunks_buffer: List[Dict[str, object]] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self._STREAM_FLUSH_INTERVAL_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_pending_chunks)
        self._is_first_chunk = True
        self._is_flushing = False

        self._pending_loader_root: Optional[Path] = None
        self._deferred_incremental_refresh: Optional[Path] = None
        self._active_filter: Optional[str] = None

        self._facade.linksUpdated.connect(self.handle_links_updated)
        self._facade.assetUpdated.connect(self.handle_asset_updated)
        self._facade.scanChunkReady.connect(self._on_scan_chunk_ready)

    def album_root(self) -> Optional[Path]:
        """Return the path of the currently open album, if any."""

        return self._album_root

    def metadata_for_absolute_path(self, path: Path) -> Optional[Dict[str, object]]:
        """Return the cached metadata row for *path* if it belongs to the model.

        The asset grid frequently passes absolute filesystem paths around when
        triggering operations such as copy or delete.  Internally the model
        indexes rows by their path relative to :attr:`_album_root`, so this
        helper normalises the provided *path* to the same representation and
        resolves the matching row when possible.  When the file no longer sits
        inside the current root—because it was moved externally or is part of a
        transient virtual collection—the method gracefully falls back to a
        direct absolute comparison so callers still receive metadata whenever it
        is available.
        """

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
        for row in rows:
            if str(row.get("abs")) == normalized_str:
                return row
        # Fall back to the recently removed cache so operations triggered right
        # after an optimistic removal can still access metadata that is no
        # longer present in the live dataset.  The cache mirrors the structure
        # of the active rows, therefore callers can interact with the returned
        # dictionary exactly as if the row were still part of the model.
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

        updated_rows = self._state_manager.finalise_move_results(moves, self._album_root)

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
    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # type: ignore[override]
        """Expose items as editable to allow QML updates."""
        default_flags = super().flags(index)
        if index.isValid():
            return default_flags | Qt.ItemFlag.ItemIsEditable
        return default_flags

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

        if role == Roles.IS_CURRENT:
            normalized = bool(value)
            row = rows[index.row()]
            if bool(row.get("is_current", False)) == normalized:
                return True
            row["is_current"] = normalized
            self.dataChanged.emit(index, index, [Roles.IS_CURRENT])
            return True

        if role == Roles.IS_SELECTED:
            normalized = bool(value)
            row = rows[index.row()]
            if bool(row.get("is_selected", False)) == normalized:
                return True
            row["is_selected"] = normalized
            self.dataChanged.emit(index, index, [Roles.IS_SELECTED])
            return True

        return super().setData(index, value, role)

    def thumbnail_loader(self) -> ThumbnailLoader:
        return self._cache_manager.thumbnail_loader()

    def get_internal_row(self, row_index: int) -> Optional[Dict[str, object]]:
        """Return the raw dictionary for *row_index* to bypass the Qt role API."""

        rows = self._state_manager.rows
        if not (0 <= row_index < len(rows)):
            return None
        return rows[row_index]

    def invalidate_thumbnail(self, rel: str) -> Optional[QModelIndex]:
        """Remove cached thumbnails and notify views for *rel*.

        Returns the :class:`QModelIndex` of the invalidated row if it exists
        in the current model snapshot.
        """

        if not rel:
            return None
        self._cache_manager.remove_thumbnail(rel)
        loader = self._cache_manager.thumbnail_loader()
        loader.invalidate(rel)
        row_index = self._state_manager.row_lookup.get(rel)
        rows = self._state_manager.rows
        if row_index is None or not (0 <= row_index < len(rows)):
            return None

        # Force a revision bump so QML reload logic detects the change
        rows[row_index]["thumbnail_rev"] = rows[row_index].get("thumbnail_rev", 0) + 1

        model_index = self.index(row_index, 0)
        self.dataChanged.emit(
            model_index, model_index, [Qt.DecorationRole, Roles.THUMBNAIL_REV]
        )
        return model_index

    # ------------------------------------------------------------------
    # Facade callbacks
    # ------------------------------------------------------------------
    def prepare_for_album(self, root: Path) -> None:
        """Reset internal state so *root* becomes the active album."""

        if self._data_loader.is_running():
            self._data_loader.cancel()
        self._state_manager.clear_reload_pending()
        self._album_root = root
        self._cache_manager.reset_for_album(root)
        self._set_deferred_incremental_refresh(None)

        self._pending_chunks_buffer = []
        self._flush_timer.stop()
        self._is_flushing = False

        self.beginResetModel()
        self._state_manager.clear_rows()
        self.endResetModel()
        self._cache_manager.clear_recently_removed()
        self._state_manager.set_virtual_reload_suppressed(False)
        self._state_manager.set_virtual_move_requires_revisit(False)
        self._pending_loader_root = None

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
        """
        Apply a new filter mode and trigger a reload if necessary.

        Changing the filter mode will cause the model to perform a full reload of the dataset
        from the database by calling `start_load()`. This operation will clear the current view
        and repopulate the model with the filtered data. Be aware that this may have performance
        implications, especially for large datasets, as the entire model is reset and reloaded.
        """
        normalized = mode.casefold() if isinstance(mode, str) and mode else None
        if normalized == self._active_filter:
            return

        self._active_filter = normalized

        # Clear data immediately to avoid "ghosting" (showing stale data while the
        # new filter is being processed asynchronously).
        self.beginResetModel()
        self._state_manager.clear_rows()
        self.endResetModel()

        self.start_load()

    def active_filter_mode(self) -> Optional[str]:
        return self._active_filter

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------
    def start_load(self) -> None:
        if not self._album_root:
            return
        if self._data_loader.is_running():
            self._data_loader.cancel()
            self._state_manager.mark_reload_pending()
            return

        self._pending_chunks_buffer = []
        self._flush_timer.stop()
        self._is_first_chunk = True
        self._is_flushing = False

        self._cache_manager.clear_recently_removed()

        manifest = self._facade.current_album.manifest if self._facade.current_album else {}
        featured = manifest.get("featured", []) or []

        # Remember which album root is being populated so chunk handlers know
        # the incoming data belongs to the active view.
        self._pending_loader_root = self._album_root

        filter_params = {}
        if self._active_filter:
            filter_params["filter_mode"] = self._active_filter

        try:
            self._data_loader.start(self._album_root, featured, filter_params=filter_params)
        except RuntimeError:
            self._state_manager.mark_reload_pending()
            self._pending_loader_root = None
            return

        self._state_manager.clear_reload_pending()

    def _on_loader_chunk_ready(self, root: Path, chunk: List[Dict[str, object]]) -> None:
        if (
            not self._album_root
            or root != self._album_root
            or not chunk
            or self._pending_loader_root != self._album_root
        ):
            return

        if self._is_first_chunk:
            self._is_first_chunk = False

            # First chunk: Reset model immediately
            self.beginResetModel()
            self._state_manager.clear_rows()
            self._state_manager.append_chunk(chunk)
            self.endResetModel()

            # Start loading thumbnails for the first batch immediately
            self.prioritize_rows(0, len(chunk) - 1)

            return

        # Subsequent chunks: Buffer and throttle
        self._pending_chunks_buffer.extend(chunk)

        if len(self._pending_chunks_buffer) >= self._STREAM_FLUSH_THRESHOLD:
            self._flush_pending_chunks()
        elif not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending_chunks(self) -> None:
        """Commit buffered chunks to the model."""
        if self._is_flushing:
            return
        if not self._pending_chunks_buffer:
            return

        self._is_flushing = True
        try:
            payload = self._pending_chunks_buffer
            self._pending_chunks_buffer = []
            self._flush_timer.stop()

            start_row = self._state_manager.row_count()
            end_row = start_row + len(payload) - 1

            self.beginInsertRows(QModelIndex(), start_row, end_row)
            self._state_manager.append_chunk(payload)
            self.endInsertRows()

            self._state_manager.on_external_row_inserted(start_row, len(payload))
        finally:
            self._is_flushing = False

    def _on_scan_chunk_ready(self, root: Path, chunk: List[Dict[str, object]]) -> None:
        """Integrate fresh rows from the scanner into the live view."""

        if not self._album_root or root != self._album_root or not chunk:
            return

        # Scanner rows are raw metadata dictionaries.  We must transform them
        # into full asset entries (including derived fields like `is_live`)
        # so the model behaves consistently regardless of the data source.
        manifest = self._facade.current_album.manifest if self._facade.current_album else {}
        featured = manifest.get("featured", []) or []
        featured_set = normalize_featured(featured)

        entries: List[Dict[str, object]] = []
        for row in chunk:
            rel = row.get("rel")
            if rel and normalise_rel_value(rel) in self._state_manager.row_lookup:
                continue

            entry = build_asset_entry(
                root, row, featured_set
            )
            if entry is not None:
                # Apply active filter constraints to prevent pollution during rescans
                if self._active_filter == "videos" and not entry.get("is_video"):
                    continue
                if self._active_filter == "live" and not entry.get("is_live"):
                    continue
                if self._active_filter == "favorites" and not entry.get("featured"):
                    continue

                entries.append(entry)

        if entries:
            # For live scanning, we just append directly as it's not high-frequency streaming
            start_row = self._state_manager.row_count()
            end_row = start_row + len(entries) - 1
            self.beginInsertRows(QModelIndex(), start_row, end_row)
            self._state_manager.append_chunk(entries)
            self.endInsertRows()
            self._state_manager.on_external_row_inserted(start_row, len(entries))

    def _on_loader_progress(self, root: Path, current: int, total: int) -> None:
        if not self._album_root or root != self._album_root:
            return
        self.loadProgress.emit(root, current, total)

    def _on_loader_finished(self, root: Path, success: bool) -> None:
        if not self._album_root or root != self._album_root:
            should_restart = self._state_manager.consume_pending_reload(self._album_root, root)
            if should_restart:
                QTimer.singleShot(0, self.start_load)
            return

        # Ensure any remaining items are committed before announcing completion.
        self._flush_pending_chunks()

        self.loadFinished.emit(root, success)

        self._pending_loader_root = None

        if (
            success
            and self._album_root
            and self._deferred_incremental_refresh
            and normalise_for_compare(self._album_root)
            == self._deferred_incremental_refresh
        ):
            logger.debug(
                "AssetListModel: applying deferred incremental refresh for %s after loader completion.",
                self._album_root,
            )
            pending_root = self._album_root
            self._set_deferred_incremental_refresh(None)
            self._refresh_rows_from_index(pending_root)

        should_restart = self._state_manager.consume_pending_reload(self._album_root, root)
        if should_restart:
            QTimer.singleShot(0, self.start_load)

    def _on_loader_error(self, root: Path, message: str) -> None:
        if not self._album_root or root != self._album_root:
            should_restart = self._state_manager.consume_pending_reload(self._album_root, root)
            self.loadFinished.emit(root, False)
            if should_restart:
                QTimer.singleShot(0, self.start_load)
            return

        self._facade.errorRaised.emit(message)
        self.loadFinished.emit(root, False)

        self._pending_chunks_buffer = []
        self._flush_timer.stop()
        self._pending_loader_root = None

        should_restart = self._state_manager.consume_pending_reload(self._album_root, root)
        if should_restart:
            QTimer.singleShot(0, self.start_load)

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

        album_root = normalise_for_compare(self._album_root)
        updated_root = normalise_for_compare(Path(root))

        if not self._links_update_targets_current_view(album_root, updated_root):
            logger.debug(
                "AssetListModel: linksUpdated for %s does not affect current root %s.",
                updated_root,
                album_root,
            )
            return

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
                # With the schema update, we must trigger an incremental refresh from DB.
                self._refresh_rows_from_index(self._album_root)
            return

        logger.debug(
            "AssetListModel: linksUpdated for %s triggers incremental refresh of %s.",
            updated_root,
            album_root,
        )

        descendant_root = updated_root if updated_root != album_root else None

        if self._state_manager.rows:
            # We used to call _reload_live_metadata here, but it relied on reading links.json synchronously.
            # Now we use the DB as the source of truth, so we refresh rows from the index.
            self._refresh_rows_from_index(self._album_root, descendant_root=descendant_root)

        if not self._state_manager.rows or self._pending_loader_root:
            logger.debug(
                "AssetListModel: deferring incremental refresh for %s until the loader completes.",
                updated_root,
            )
            self._set_deferred_incremental_refresh(self._album_root)
            return

        if self._data_loader.is_running():
            logger.debug(
                "AssetListModel: loader active, postponing incremental refresh for %s.",
                updated_root,
            )
            self._set_deferred_incremental_refresh(self._album_root)
            return

        self._set_deferred_incremental_refresh(None)
        self._refresh_rows_from_index(self._album_root, descendant_root=descendant_root)

    def _refresh_rows_from_index(
        self, root: Path, descendant_root: Optional[Path] = None
    ) -> None:
        """Synchronise the model with the latest index snapshot for *root*.

        The helper performs a synchronous load of ``index.jsonl`` so the model
        can calculate a diff without forcing a full reset.  This is lightweight
        compared to launching a new background worker and avoids the flicker
        caused by ``beginResetModel``.
        """

        manifest = self._facade.current_album.manifest if self._facade.current_album else {}
        featured = manifest.get("featured", []) or []

        filter_params = {}
        if self._active_filter:
            filter_params["filter_mode"] = self._active_filter

        try:
            fresh_rows, _ = self._data_loader.compute_rows(
                root, featured, filter_params=filter_params
            )

            # If the update came from a descendant sub-album, the parent's DB might not yet
            # reflect the changes (e.g., favorite status). We explicitly fetch the rows
            # from the descendant's DB and merge them into the parent's row set.
            if descendant_root and descendant_root != root:
                # Load the descendant's manifest to get the fresh 'featured' list.
                # This ensures that even if the DB is stale (is_favorite=0), the manifest
                # will provide the correct status.
                try:
                    child_album = Album.open(descendant_root)
                    child_featured = child_album.manifest.get("featured", [])
                except (IPhotoError, OSError, ValueError) as exc:
                    logger.error(
                        "AssetListModel: failed to load manifest for %s: %s",
                        descendant_root,
                        exc,
                    )
                    child_featured = []

                child_rows, _ = self._data_loader.compute_rows(
                    descendant_root, child_featured, filter_params=filter_params
                )
                if child_rows:
                    # Map fresh rows by rel for O(1) update
                    fresh_lookup = {
                        normalise_rel_value(row.get("rel")): i
                        for i, row in enumerate(fresh_rows)
                    }

                    rel_prefix = descendant_root.relative_to(root)
                    prefix_str = rel_prefix.as_posix()

                    for child_row in child_rows:
                        child_rel = child_row.get("rel")
                        if not child_rel:
                            continue

                        # Adjust child rel to be relative to the parent root
                        # Use string concatenation for performance instead of Path objects
                        # Ensure forward slashes for consistency
                        child_rel_str = str(child_rel).replace("\\", "/")
                        # Use PurePosixPath to join paths reliably, avoiding leading slashes
                        from pathlib import PurePosixPath
                        adjusted_rel = PurePosixPath(prefix_str, child_rel_str).as_posix()
                        normalized_key = normalise_rel_value(adjusted_rel)

                        if normalized_key in fresh_lookup:
                            # Create a copy to avoid mutating data potentially shared or cached
                            merged_row = child_row.copy()
                            # Update the child row with adjusted rel
                            merged_row["rel"] = adjusted_rel
                            # Since ID might be different or same depending on implementation,
                            # we trust the match by rel/abs path.
                            # We replace the stale row in fresh_rows with the fresh child_row.
                            fresh_rows[fresh_lookup[normalized_key]] = merged_row

        except Exception as exc:  # pragma: no cover - surfaced via GUI
            logger.error(
                "AssetListModel: incremental refresh for %s failed: %s", root, exc
            )
            self._facade.errorRaised.emit(str(exc))
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

        if not current_rows and not diff.is_reset:
            # If we ended up empty after removals
            self._cache_manager.reset_caches_for_new_rows([])

        self._state_manager.rebuild_lookup()

        # Update data for changed rows
        for replacement in diff.changed_items:
            rel_key = normalise_rel_value(replacement.get("rel"))
            if not rel_key:
                continue

            # Look up the current index of the item using its rel key.
            # We use the rebuilt lookup table which reflects the structure
            # after insertions and removals.
            row_index = self._state_manager.row_lookup.get(rel_key)
            if row_index is None or not (0 <= row_index < len(current_rows)):
                continue

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

            self.invalidate_thumbnail(rel_key)

        return diff.structure_changed or bool(diff.changed_items)

    def _set_deferred_incremental_refresh(self, root: Optional[Path]) -> None:
        """Remember that an incremental refresh should run once loading settles."""

        if root is None:
            self._deferred_incremental_refresh = None
            return
        self._deferred_incremental_refresh = normalise_for_compare(root)

    def _links_update_targets_current_view(
        self, album_root: Path, updated_root: Path
    ) -> bool:
        """Return ``True`` when ``links.json`` updates should refresh the model.

        The method compares the normalised path of the dataset currently exposed
        by the model with the path for which the backend rebuilt ``links.json``.
        A refresh is required in two situations:

        * The backend updated ``links.json`` for the exact same root that feeds
          the model.
        * The model shows a library-wide view (for example "All Photos" or
          "Live Photos") and the backend refreshed ``links.json`` for an album
          living under that library root.

        Normalising via :func:`os.path.realpath` and :func:`os.path.normcase`
        ensures that comparisons remain stable across platforms and symbolic
        link setups where the same directory may be referenced through different
        aliases.
        """

        if album_root == updated_root:
            return True

        return is_descendant_path(updated_root, album_root)
