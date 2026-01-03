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
    QMutex,
    QMutexLocker, QThreadPool,
)
from PySide6.QtGui import QPixmap

from ..tasks.thumbnail_loader import ThumbnailLoader
from ..tasks.asset_loader_worker import (
    build_asset_entry,
    normalize_featured,
    LiveIngestWorker,
    AssetLoaderSignals,
)
from ..tasks.incremental_refresh_worker import IncrementalRefreshSignals, IncrementalRefreshWorker
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
    _STREAM_BATCH_SIZE = 100
    _STREAM_FLUSH_THRESHOLD = 2000

    def __init__(self, facade: "AppFacade", parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._facade = facade
        self._album_root: Optional[Path] = None
        self._thumb_size = QSize(512, 512)

        # Try to acquire library root early if available
        library_root = None
        if self._facade.library_manager:
            library_root = self._facade.library_manager.root()

        self._cache_manager = AssetCacheManager(self._thumb_size, self, library_root=library_root)
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
        self._pending_rels: set[str] = set()
        self._pending_abs: set[str] = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self._STREAM_FLUSH_INTERVAL_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_pending_chunks)
        self._is_first_chunk = True
        self._is_flushing = False

        self._pending_finish_event: Optional[Tuple[Path, bool]] = None
        self._pending_loader_root: Optional[Path] = None
        self._deferred_incremental_refresh: Optional[Path] = None
        self._active_filter: Optional[str] = None
        self._ignore_incoming_chunks: bool = False

        self._incremental_worker: Optional[IncrementalRefreshWorker] = None
        self._incremental_signals: Optional[IncrementalRefreshSignals] = None
        self._refresh_lock = QMutex()
        self._current_live_worker: Optional[LiveIngestWorker] = None

        self._facade.linksUpdated.connect(self.handle_links_updated)
        self._facade.assetUpdated.connect(self.handle_asset_updated)
        self._facade.scanChunkReady.connect(self._on_scan_chunk_ready)

    def set_library_root(self, root: Path) -> None:
        """Update the centralized library root for thumbnail generation and index access."""
        self._cache_manager.set_library_root(root)
        self._data_loader.set_library_root(root)

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

        # O(1) Lookup optimization
        row_index = self._state_manager.get_index_by_abs(normalized_str)
        if row_index is not None and 0 <= row_index < len(rows):
            return rows[row_index]

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
        model_index = self.index(row_index, 0)
        self.dataChanged.emit(model_index, model_index, [Qt.DecorationRole])
        return model_index

    # ------------------------------------------------------------------
    # Facade callbacks
    # ------------------------------------------------------------------
    def prepare_for_album(self, root: Path) -> None:
        """Reset internal state so *root* becomes the active album."""

        if self._data_loader.is_running():
            self._data_loader.cancel()
            self._ignore_incoming_chunks = True
        else:
            self._ignore_incoming_chunks = False

        self._state_manager.clear_reload_pending()
        self._album_root = root
        self._cache_manager.reset_for_album(root)
        self._set_deferred_incremental_refresh(None)

        self._pending_chunks_buffer = []
        self._pending_rels.clear()
        self._pending_abs.clear()
        self._flush_timer.stop()
        self._pending_finish_event = None
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

        # --- NEW OPTIMIZATION: In-Memory Filtering ---
        # If we have a reasonable number of items in memory, try to filter locally
        # instead of hitting the DB.

        # Threshold: 10,000 items (arbitrary, can tune).
        # We also need to check if the current model data is "complete" enough to support filtering.
        # But wait, AssetListModel typically only holds what is loaded.
        # If we are currently filtered to "Videos" (subset) and switch to "All Photos" (superset),
        # we MUST reload because we don't have the photos in RAM.
        #
        # If we are currently "All Photos" (superset) and switch to "Videos" (subset),
        # we COULD filter in memory.
        #
        # For this PR, the key requirement is "Seamless transition".
        # If we switch filters, we trigger `start_load()`.
        # `start_load()` will now merge DB data + Live Buffer.
        # The Live Buffer helps fill gaps.
        #
        # For now, I will stick to the reload-based approach (safer) but augmented with the live buffer.
        # Implementing robust In-Memory filtering requires holding the FULL dataset always,
        # which defeats the purpose of pagination/loading optimization if we implemented that.
        # However, this app currently loads EVERYTHING into AssetListModel RAM anyway (it's not paginated).
        #
        # So, if we are switching to a subset, we could just hide rows.
        # But QAbstractListModel with `QSortFilterProxyModel` is usually the Qt way.
        # Here we are managing rows manually.
        #
        # Let's rely on `start_load()` being fast enough thanks to the Live Buffer integration.
        # The user requirement said: "Try in-memory filtering... OR ensure seamless loading".
        # I will focus on the "Hybrid Loading" in `start_load` first as it solves the "missing data" problem
        # regardless of filter direction.

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
            self._ignore_incoming_chunks = True
            # Clear buffer immediately to avoid committing stale chunks if logic leaks
            self._pending_chunks_buffer = []
            self._flush_timer.stop()
            self._is_first_chunk = True
            self._state_manager.mark_reload_pending()
            return

        self._pending_chunks_buffer = []
        self._pending_rels.clear()
        self._pending_abs.clear()
        self._flush_timer.stop()
        self._pending_finish_event = None
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
            self._ignore_incoming_chunks = False

            # Inject any live (recently scanned but not yet persisted) items.
            # CRITICAL OPTIMIZATION: Process these items in a background thread.
            # Building 200+ asset entries (decoding thumbs, resolving geo) on the
            # main thread causes visible UI lag.
            if self._facade.library_manager:
                try:
                    # Cancel any existing live worker to prevent race conditions or wasted work
                    if self._current_live_worker:
                        self._current_live_worker.cancel()
                        self._current_live_worker = None

                    live_items = self._facade.library_manager.get_live_scan_results(relative_to=self._album_root)
                    if live_items:
                        # Create dedicated signals for the live ingest worker.
                        # We use a dedicated signal object to avoid interfering with the main loader's state,
                        # but connect to the same slot (_on_loader_chunk_ready) which handles buffering/deduplication.
                        live_signals = AssetLoaderSignals(self)
                        live_signals.chunkReady.connect(self._on_loader_chunk_ready)
                        live_signals.finished.connect(lambda _, __: live_signals.deleteLater())

                        worker = LiveIngestWorker(
                            self._album_root,
                            live_items,
                            featured,
                            live_signals,
                            filter_params=filter_params,
                        )
                        self._current_live_worker = worker
                        QThreadPool.globalInstance().start(worker)
                except Exception as e:
                    logger.error("Failed to inject live scan results: %s", e, exc_info=True)

        except RuntimeError:
            self._state_manager.mark_reload_pending()
            self._pending_loader_root = None
            return

        self._state_manager.clear_reload_pending()

    def _on_loader_chunk_ready(self, root: Path, chunk: List[Dict[str, object]]) -> None:
        if self._ignore_incoming_chunks:
            return

        if (
            not self._album_root
            or root != self._album_root
            or not chunk
            or self._pending_loader_root != self._album_root
        ):
            return

        # Deduplicate incoming chunk against what we already have (e.g. from live buffer)
        # The loader reads from DB. The live buffer came from Scanner.
        # They might overlap if the scanner persisted data and the loader picked it up.
        unique_chunk = []
        for row in chunk:
            rel = row.get("rel")
            if not rel:
                continue
            norm_rel = normalise_rel_value(rel)
            abs_val = row.get("abs")
            abs_key = str(abs_val) if abs_val else None
            
            # Only add if not already present in MODEL or PENDING BUFFER
            # Check both rel and abs to prevent duplicates
            if (
                norm_rel not in self._state_manager.row_lookup
                and norm_rel not in self._pending_rels
                and (not abs_key or (
                    self._state_manager.get_index_by_abs(abs_key) is None
                    and abs_key not in self._pending_abs
                ))
            ):
                unique_chunk.append(row)
                self._pending_rels.add(norm_rel)
                if abs_key:
                    self._pending_abs.add(abs_key)

        if not unique_chunk:
            return

        chunk = unique_chunk

        if self._is_first_chunk:
            self._is_first_chunk = False

            # If we already have rows (from live buffer), treat this chunk as subsequent rather than resetting.
            if self._state_manager.row_count() > 0:
                # We have data (live buffer). Treat this chunk as subsequent.
                self._pending_chunks_buffer.extend(chunk)
                if len(self._pending_chunks_buffer) >= self._STREAM_FLUSH_THRESHOLD:
                    self._flush_pending_chunks()
                elif not self._flush_timer.isActive():
                    self._flush_timer.start()
            else:
                self.beginResetModel()
                self._state_manager.clear_rows()
                self._state_manager.append_chunk(chunk)
                self.endResetModel()

                # Cleanup pending rels and abs for items we just inserted immediately
                for row in chunk:
                    rel = row.get("rel")
                    if rel:
                        self._pending_rels.discard(normalise_rel_value(rel))
                    abs_val = row.get("abs")
                    if abs_val:
                        self._pending_abs.discard(str(abs_val))

                self.prioritize_rows(0, len(chunk) - 1)

            return

        # Subsequent chunks: Buffer and throttle
        self._pending_chunks_buffer.extend(chunk)

        if len(self._pending_chunks_buffer) >= self._STREAM_FLUSH_THRESHOLD:
            self._flush_pending_chunks()
        elif not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending_chunks(self) -> None:
        """Commit buffered chunks to the model in small batches to keep UI responsive."""
        if self._is_flushing:
            return
        if not self._pending_chunks_buffer:
            return

        self._is_flushing = True
        try:
            # 1. Slice: take only the first N items to avoid freezing the UI
            batch_size = self._STREAM_BATCH_SIZE
            payload = self._pending_chunks_buffer[:batch_size]

            # 2. Leave the rest for the next Timer tick
            remainder = self._pending_chunks_buffer[batch_size:]
            self._pending_chunks_buffer = remainder

            # If data remains, ensure the timer continues with a short interval.
            # When a finish event is pending, use an immediate timeout to avoid visible stalls.
            if self._pending_chunks_buffer:
                interval = 0 if self._pending_finish_event else self._STREAM_FLUSH_INTERVAL_MS
                self._flush_timer.start(interval)
            else:
                self._flush_timer.stop()

            start_row = self._state_manager.row_count()
            end_row = start_row + len(payload) - 1

            self.beginInsertRows(QModelIndex(), start_row, end_row)
            self._state_manager.append_chunk(payload)
            self.endInsertRows()

            # Cleanup pending sets for committed items after successful insertion
            for row in payload:
                rel = row.get("rel")
                if rel:
                    self._pending_rels.discard(normalise_rel_value(rel))
                abs_val = row.get("abs")
                if abs_val:
                    self._pending_abs.discard(str(abs_val))

            self._state_manager.on_external_row_inserted(start_row, len(payload))

            # After processing this batch, if buffer is empty and we have a pending finish, finalize it.
            if self._pending_finish_event and not self._pending_chunks_buffer:
                self._finalize_loading(*self._pending_finish_event)

        finally:
            self._is_flushing = False

    def _on_scan_chunk_ready(self, root: Path, chunk: List[Dict[str, object]]) -> None:
        """Integrate fresh rows from the scanner into the live view."""

        if not self._album_root or not chunk:
            return

        # Ensure asset paths are correctly interpreted relative to the current album view.
        # If the scan root and album root differ, re-base or filter asset paths so that
        # they are relative to the album root as expected by AssetListModel.
        try:
            scan_root = root.resolve()
            view_root = self._album_root.resolve()
        except OSError as exc:
            logger.warning("Failed to resolve paths during scan chunk processing: %s", exc)
            return

        is_direct_match = (scan_root == view_root)
        is_scan_parent_of_view = (scan_root in view_root.parents)

        if not (is_direct_match or is_scan_parent_of_view):
            return

        manifest = self._facade.current_album.manifest if self._facade.current_album else {}
        featured = manifest.get("featured", []) or []
        featured_set = normalize_featured(featured)

        entries: List[Dict[str, object]] = []
        for row in chunk:
            # `row['rel']` is relative to `scan_root`
            raw_rel = row.get("rel")
            if not raw_rel:
                continue

            full_path = scan_root / raw_rel

            # Check if file is inside view_root
            try:
                view_rel = full_path.relative_to(view_root).as_posix()
            except ValueError:
                # File not inside current view
                continue
            except OSError as e:
                logger.error(
                    "OSError while checking if %s is relative to %s: %s",
                    full_path, view_root, e
                )
                continue

            # Re-check uniqueness using the VIEW relative path
            if normalise_rel_value(view_rel) in self._state_manager.row_lookup:
                continue

            # Re-base the row's 'rel' path to be relative to the current view root.
            # Creates a shallow copy to avoid modifying the original row dict.
            adjusted_row = row.copy()
            adjusted_row['rel'] = view_rel

            entry = build_asset_entry(
                view_root, adjusted_row, featured_set
            )

            if entry is not None:
                if self._active_filter == "videos" and not entry.get("is_video"):
                    continue
                if self._active_filter == "live" and not entry.get("is_live"):
                    continue
                if self._active_filter == "favorites" and not entry.get("featured"):
                    continue

                entries.append(entry)

        if entries:
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
        if self._ignore_incoming_chunks:
            should_restart = self._state_manager.consume_pending_reload(self._album_root, root)
            self._ignore_incoming_chunks = False
            self._pending_loader_root = None

            # Defensive programming: clear any buffered chunks to prevent state leakage
            self._pending_chunks_buffer = []
            self._pending_rels.clear()
            self._pending_abs.clear()
            self._flush_timer.stop()
            self._pending_finish_event = None

            self.loadFinished.emit(root, success)
            if should_restart:
                QTimer.singleShot(0, self.start_load)
            return

        if not self._album_root or root != self._album_root:
            should_restart = self._state_manager.consume_pending_reload(self._album_root, root)
            if should_restart:
                QTimer.singleShot(0, self.start_load)
            return

        # If buffer is empty, finalize immediately.
        # Otherwise, store state and let _flush_pending_chunks handle it.
        if not self._pending_chunks_buffer:
            self._finalize_loading(root, success)
        else:
            self._pending_finish_event = (root, success)
            # Drain any remaining buffered chunks immediately to avoid end-of-load stalls.
            self._flush_pending_chunks()

    def _finalize_loading(self, root: Path, success: bool) -> None:
        """Emit loadFinished and handle post-load tasks."""
        self._pending_finish_event = None
        self._flush_timer.stop()

        self.loadFinished.emit(root, success)

        # Only clear pending_loader_root AFTER strictly everything is done
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
        self._pending_rels.clear()
        self._pending_abs.clear()
        self._flush_timer.stop()
        self._pending_finish_event = None
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

        This method spawns an :class:`IncrementalRefreshWorker` on a background
        thread to load the data and calculate the diff, avoiding UI blocking on
        the main thread.
        """
        # Spawn a background worker to refresh data incrementally without blocking the UI.

        with QMutexLocker(self._refresh_lock):
            if self._incremental_worker is not None:
                # Already refreshing, skip this request or queue it?
                # For now, let's just log and skip, relying on subsequent updates or the current one being enough.
                logger.debug("AssetListModel: incremental refresh already in progress, skipping request.")
                return

            manifest = self._facade.current_album.manifest if self._facade.current_album else {}
            featured = manifest.get("featured", []) or []

            filter_params = {}
            if self._active_filter:
                filter_params["filter_mode"] = self._active_filter

            self._incremental_signals = IncrementalRefreshSignals()
            self._incremental_signals.resultsReady.connect(self._apply_incremental_results)
            self._incremental_signals.error.connect(self._on_incremental_error)

            self._incremental_worker = IncrementalRefreshWorker(
                root,
                featured,
                self._incremental_signals,
                filter_params=filter_params,
                descendant_root=descendant_root
            )

            QThreadPool.globalInstance().start(self._incremental_worker)

    def _on_incremental_error(self, root: Path, message: str) -> None:
        logger.error("AssetListModel: incremental refresh error for %s: %s", root, message)
        self._cleanup_incremental_worker()

    def _cleanup_incremental_worker(self) -> None:
        with QMutexLocker(self._refresh_lock):
            if self._incremental_signals:
                try:
                    self._incremental_signals.resultsReady.disconnect(self._apply_incremental_results)
                    self._incremental_signals.error.disconnect(self._on_incremental_error)
                except RuntimeError:
                    # Ignore errors if signals were already disconnected
                    pass
                self._incremental_signals.deleteLater()
                self._incremental_signals = None
            # Reset worker inside lock to prevent race
            self._incremental_worker = None

    def _apply_incremental_results(self, root: Path, fresh_rows: List[Dict[str, object]]) -> None:
        """Apply the fetched rows to the model via diffing."""

        if not self._album_root or root != self._album_root:
            self._cleanup_incremental_worker()
            return

        self._cleanup_incremental_worker()

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
            # Individual removals clear per-item caches above; this pass reconciles
            # the remaining cache entries with the final dataset to drop any
            # lingering thumbnails from deleted assets.
            self._cache_manager.reset_caches_for_new_rows(current_rows)

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

            # Check if the change requires invalidating the thumbnail cache.
            # We skip invalidation if only metadata that doesn't affect the visual
            # thumbnail (like favorite status or live photo role) has changed.
            if self._should_invalidate_thumbnail(original, replacement):
                self.invalidate_thumbnail(rel_key)

        return diff.structure_changed or bool(diff.changed_items)

    def _should_invalidate_thumbnail(
        self, old_row: Dict[str, object], new_row: Dict[str, object]
    ) -> bool:
        """Return True if the thumbnail must be regenerated based on row changes."""
        # Visual fields that definitely affect the thumbnail
        # 'ts' (timestamp) is the primary versioning key.
        # 'bytes' (filesize) implies content change.
        # 'abs' (absolute path) implies file location change.
        # 'w'/'h' affect aspect ratio and cropping.
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

        # If only keys like 'is_favorite', 'live_role', 'location', 'gps', 'year', 'month'
        # changed, we can safely keep the existing thumbnail.
        return False

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
