"""Controller for managing asset data loading, streaming, and incremental refreshes.

This module implements a Pull-Based K-Way Merge architecture for efficiently
loading large aggregate views (e.g., "All Photos", "Videos"). The key components:

1. **MergedAssetStream**: A K-Way merge buffer that combines DB and Live Scanner
   data streams in O(N) linear time, avoiding the O(N log N) cost of re-sorting.

2. **Lazy Loading**: Data is fetched on-demand when the UI requests it via
   `load_next_page()`, rather than eagerly loading all data upfront.

3. **Cursor-based Pagination**: The DB stream uses cursor-based pagination to
   fetch pages incrementally as needed.

Architecture:
    UI Scroll -> fetchMore() -> Controller.load_next_page()
    -> MergedAssetStream.pop_next() -> [If buffer low] -> Fetch DB page
    -> Push to MergedAssetStream -> Pop merged data -> Emit to UI
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import (
    QObject,
    QTimer,
    Signal,
    QMutex,
    QMutexLocker,
    QThreadPool,
)

from ...tasks.asset_loader_worker import (
    LiveIngestWorker,
    AssetLoaderSignals,
    PaginatedLoaderSignals,
    PaginatedLoaderWorker,
    DEFAULT_PAGE_SIZE,
    build_asset_entry,
    normalize_featured,
)
from ...tasks.incremental_refresh_worker import (
    IncrementalRefreshSignals,
    IncrementalRefreshWorker,
)
from ..asset_data_loader import AssetDataLoader
from .streaming import MergedAssetStream
from .....utils.pathutils import (
    normalise_for_compare,
    is_descendant_path,
    normalise_rel_value,
)

logger = logging.getLogger(__name__)


class AssetListController(QObject):
    """
    Manages data loading, buffering, and background tasks for the asset list.

    Implements a Pull-Based K-Way Merge architecture where:
    - Data is fetched lazily when the UI requests it via fetchMore()
    - DB and Live Scanner streams are merged in O(N) linear time
    - The MergedAssetStream acts as the central data buffer
    """

    # Signals
    # Emits (chunk, is_reset)
    batchReady = Signal(list, bool)
    # Emits (fresh_rows, root)
    incrementalReady = Signal(list, Path)
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)
    error = Signal(Path, str)
    # Pagination signals
    allDataLoaded = Signal()  # Signals when all data has been loaded (end of cursor)
    # New signal for first screen loaded
    initialPageLoaded = Signal(Path)  # Emitted when the first page is ready

    # Tuning constants
    _STREAM_FLUSH_INTERVAL_MS = 100
    _STREAM_BATCH_SIZE = 100
    _STREAM_FLUSH_THRESHOLD = 2000
    _PREFETCH_THRESHOLD = 50  # Fetch more DB data when buffer drops below this
    _PREFETCH_MULTIPLIER = 2  # Fetch N times the requested page size for efficiency
    
    # Lazy loading configuration
    LAZY_LOADING_THRESHOLD = 1000  # Enable lazy loading for albums > 1000 items
    INITIAL_PAGE_SIZE = 500  # First screen items
    PREFETCH_PAGES = 2  # Number of pages to prefetch in background
    PREFETCH_DELAY_MS = 500  # Delay before starting background prefetch

    def __init__(
        self,
        facade: Any,
        duplication_checker: Callable[[str, Optional[str]], bool],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._facade = facade
        self._duplication_checker = duplication_checker
        self._album_root: Optional[Path] = None
        self._active_filter: Optional[str] = None

        # Workers (for backward compatibility with eager loading)
        self._data_loader = AssetDataLoader(self)
        self._data_loader.chunkReady.connect(self._on_loader_chunk_ready)
        self._data_loader.loadProgress.connect(self._on_loader_progress)
        self._data_loader.loadFinished.connect(self._on_loader_finished)
        self._data_loader.error.connect(self._on_loader_error)

        self._current_live_worker: Optional[LiveIngestWorker] = None
        self._live_signals: Optional[AssetLoaderSignals] = None
        self._incremental_worker: Optional[IncrementalRefreshWorker] = None
        self._incremental_signals: Optional[IncrementalRefreshSignals] = None
        self._refresh_lock = QMutex()

        # Pagination state (cursor-based lazy loading)
        self._cursor_dt: Optional[str] = None
        self._cursor_id: Optional[str] = None
        self._is_loading_page: bool = False
        self._all_data_loaded: bool = False
        self._paginated_worker: Optional[PaginatedLoaderWorker] = None
        self._paginated_signals: Optional[PaginatedLoaderSignals] = None

        # K-Way Merge Stream - central buffer for lazy loading
        self._k_way_stream: MergedAssetStream = MergedAssetStream()
        self._use_lazy_loading: bool = False  # Enable lazy loading for large albums
        
        # Lazy loading configuration (user-configurable)
        self._lazy_mode_enabled: bool = True  # Default to enabled for performance
        self._initial_page_loaded: bool = False  # Track if first screen has loaded
        self._prefetch_timer: Optional[QTimer] = None  # Timer for background prefetch
        self._prefetch_remaining: int = 0  # Counter for remaining prefetch pages
        self._prefetch_pending: bool = False  # Track if recursive prefetch is scheduled

        # Legacy streaming buffer state (for backward compatibility)
        self._pending_chunks_buffer: List[Dict[str, object]] = []
        self._pending_rels: Set[str] = set()
        self._pending_abs: Set[str] = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self._STREAM_FLUSH_INTERVAL_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_pending_chunks)
        self._is_first_chunk = True
        self._is_flushing = False

        self._pending_finish_event: Optional[Tuple[Path, bool]] = None
        self._pending_loader_root: Optional[Path] = None
        self._deferred_incremental_refresh: Optional[Path] = None
        self._ignore_incoming_chunks: bool = False
        self._reload_pending: bool = False

        # Facade connections
        self._facade.scanChunkReady.connect(self._on_scan_chunk_ready)

    def set_library_root(self, root: Path) -> None:
        """Update the centralized library root for the loader."""
        self._data_loader.set_library_root(root)

    # ------------------------------------------------------------------
    # Lazy Loading Configuration Methods
    # ------------------------------------------------------------------
    
    def enable_lazy_loading(self, enabled: bool = True) -> None:
        """Enable or disable lazy loading mode.
        
        When enabled, the controller will use incremental loading with 
        pagination for large albums (> LAZY_LOADING_THRESHOLD items).
        
        Args:
            enabled: True to enable lazy loading, False for traditional eager loading.
        """
        self._lazy_mode_enabled = enabled
        logger.info("Lazy loading mode: %s", "enabled" if enabled else "disabled")

    def should_use_lazy_loading(self) -> bool:
        """Determine if lazy loading should be used for the current album.
        
        Returns:
            True if lazy loading should be enabled based on:
            1. Lazy mode is enabled in configuration
            2. Album root is set
            
        Note:
            When lazy mode is enabled, lazy loading is always used regardless
            of album size. The cursor-based pagination ensures efficient loading
            for any album size, so there's no downside to using it even for
            small albums.
        """
        if not self._lazy_mode_enabled:
            return False
        
        if self._album_root is None:
            return False
        
        # For performance optimization, we always use lazy loading for now
        # since the infrastructure is already in place. This can be made
        # more intelligent based on actual item count if needed.
        #
        # The cursor-based pagination ensures efficient loading regardless
        # of album size, so there's no downside to using it even for small albums.
        return True

    def _get_filter_params(self) -> Dict[str, object]:
        """Build filter parameters dictionary from current filter state."""
        filter_params: Dict[str, object] = {}
        if self._active_filter:
            filter_params["filter_mode"] = self._active_filter
        return filter_params

    # ------------------------------------------------------------------
    # Album Lifecycle
    # ------------------------------------------------------------------

    def prepare_for_album(self, root: Path) -> None:
        """Reset internal state so *root* becomes the active album."""
        if self._data_loader.is_running():
            self._data_loader.cancel()
            self._ignore_incoming_chunks = True
        else:
            self._ignore_incoming_chunks = False

        # Cancel and cleanup live worker if running
        if self._current_live_worker:
            self._current_live_worker.cancel()
            self._current_live_worker = None
        if self._live_signals:
            try:
                self._live_signals.chunkReady.disconnect(self._on_loader_chunk_ready)
            except RuntimeError:
                pass
            try:
                self._live_signals.deleteLater()
            except RuntimeError:
                pass  # C++ object already deleted
            self._live_signals = None

        # Cleanup incremental refresh worker
        self._cleanup_incremental_worker()

        # Cleanup pagination worker
        self._cleanup_paginated_worker()

        # When preparing for a new album, we should drop any pending reloads for the old one.
        self._reload_pending = False

        self._album_root = root
        self._set_deferred_incremental_refresh(None)
        self._reset_buffers()
        self._reset_pagination_state()
        self._pending_loader_root = None

    def _reset_buffers(self) -> None:
        """Clear streaming buffers."""
        self._pending_chunks_buffer = []
        self._pending_rels.clear()
        self._pending_abs.clear()
        self._flush_timer.stop()
        self._pending_finish_event = None
        self._is_flushing = False
        self._is_first_chunk = True
        # Reset K-Way merge stream
        self._k_way_stream.reset()
        self._use_lazy_loading = False
        # Reset lazy loading state
        self._initial_page_loaded = False
        # Cancel any pending prefetch timer and recursive prefetch
        if self._prefetch_timer is not None:
            self._prefetch_timer.stop()
            self._prefetch_timer = None
        self._prefetch_pending = False
        self._prefetch_remaining = 0

    def _reset_pagination_state(self) -> None:
        """Clear pagination state for a fresh load."""
        self._cursor_dt = None
        self._cursor_id = None
        self._is_loading_page = False
        self._all_data_loaded = False

    def _cleanup_paginated_worker(self) -> None:
        """Cancel and cleanup any running paginated worker."""
        if self._paginated_worker:
            self._paginated_worker.cancel()
            self._paginated_worker = None
        if self._paginated_signals:
            try:
                self._paginated_signals.pageReady.disconnect(self._on_paginated_page_ready)
                self._paginated_signals.endOfData.disconnect(self._on_paginated_end_of_data)
                self._paginated_signals.error.disconnect(self._on_paginated_error)
            except RuntimeError:
                pass
            try:
                self._paginated_signals.deleteLater()
            except RuntimeError:
                pass  # C++ object already deleted
            self._paginated_signals = None
        self._is_loading_page = False

    def set_filter_mode(self, mode: Optional[str]) -> None:
        """Update filter mode and trigger reload if changed."""
        normalized = mode.casefold() if isinstance(mode, str) and mode else None
        if normalized == self._active_filter:
            return

        self._active_filter = normalized
        self._reset_pagination_state()
        self.start_load()

    def active_filter_mode(self) -> Optional[str]:
        return self._active_filter

    def start_load(self) -> None:
        """Start loading assets for the current album root using lazy pagination.
        
        This method initializes the K-Way merge stream and triggers the first
        page fetch using cursor-based pagination. Live Scanner results are
        integrated in the background via the merge stream.
        """
        if not self._album_root:
            return

        # Cancel any running legacy loader
        if self._data_loader.is_running():
            self._reload_pending = True
            self._data_loader.cancel()
            self._ignore_incoming_chunks = True
            self._reset_buffers()
            self._is_first_chunk = True
            return

        # Cancel any running paginated worker
        self._cleanup_paginated_worker()

        self._reload_pending = False
        self._reset_buffers()
        self._reset_pagination_state()
        self._is_first_chunk = True

        # Enable lazy loading mode (uses K-Way merge stream)
        self._use_lazy_loading = True

        manifest = (
            self._facade.current_album.manifest if self._facade.current_album else {}
        )
        featured = manifest.get("featured", []) or []

        self._pending_loader_root = self._album_root
        filter_params = self._get_filter_params()

        # Ensure library_root is set from facade if not already configured
        if not self._data_loader._library_root and self._facade.library_manager:
            library_root = self._facade.library_manager.root()
            if library_root:
                self._data_loader.set_library_root(library_root)

        self._ignore_incoming_chunks = False

        # Start Live Scanner for background updates
        # Live items will be merged with DB results via K-Way stream
        if self._facade.library_manager:
            try:
                if self._current_live_worker:
                    self._current_live_worker.cancel()
                    self._current_live_worker = None
                if self._live_signals:
                    try:
                        self._live_signals.chunkReady.disconnect(self._on_loader_chunk_ready)
                    except RuntimeError:
                        pass
                    try:
                        self._live_signals.deleteLater()
                    except RuntimeError:
                        pass  # C++ object already deleted
                    self._live_signals = None

                live_items = self._facade.library_manager.get_live_scan_results(
                    relative_to=self._album_root
                )
                if live_items:
                    live_signals = AssetLoaderSignals(self)
                    live_signals.chunkReady.connect(self._on_live_chunk_ready)
                    live_signals.finished.connect(
                        lambda _, __: live_signals.deleteLater()
                    )

                    worker = LiveIngestWorker(
                        self._album_root,
                        live_items,
                        featured,
                        live_signals,
                        filter_params=filter_params,
                    )
                    self._current_live_worker = worker
                    self._live_signals = live_signals
                    QThreadPool.globalInstance().start(worker)
            except Exception as e:
                logger.error(
                    "Failed to inject live scan results: %s", e, exc_info=True
                )

        # Trigger first page load using optimized pagination
        # This replaces the legacy _data_loader.start() call
        logger.debug("start_load: triggering initial page load via lazy pagination")
        self.load_next_page()

    def _on_loader_chunk_ready(
        self, root: Path, chunk: List[Dict[str, object]]
    ) -> None:
        if self._ignore_incoming_chunks:
            return

        if (
            not self._album_root
            or root != self._album_root
            or not chunk
            or self._pending_loader_root != self._album_root
        ):
            return

        unique_chunk = []
        for row in chunk:
            rel = row.get("rel")
            if not rel:
                continue
            norm_rel = normalise_rel_value(rel)
            abs_val = row.get("abs")
            abs_key = str(abs_val) if abs_val else None

            is_duplicate_in_model = self._duplication_checker(norm_rel, abs_key)
            is_duplicate_in_buffer = (
                norm_rel in self._pending_rels
                or (abs_key and abs_key in self._pending_abs)
            )

            if not is_duplicate_in_model and not is_duplicate_in_buffer:
                unique_chunk.append(row)
                self._pending_rels.add(norm_rel)
                if abs_key:
                    self._pending_abs.add(abs_key)

        if not unique_chunk:
            return

        chunk = unique_chunk

        if self._is_first_chunk:
            self._is_first_chunk = False
            self.batchReady.emit(chunk, True)

            for row in chunk:
                rel = row.get("rel")
                if rel:
                    self._pending_rels.discard(normalise_rel_value(rel))
                abs_val = row.get("abs")
                if abs_val:
                    self._pending_abs.discard(str(abs_val))

            return

        # Subsequent chunks
        self._pending_chunks_buffer.extend(chunk)
        if len(self._pending_chunks_buffer) >= self._STREAM_FLUSH_THRESHOLD:
            self._flush_pending_chunks()
        elif not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending_chunks(self) -> None:
        if self._is_flushing or not self._pending_chunks_buffer:
            return

        self._is_flushing = True
        try:
            batch_size = self._STREAM_BATCH_SIZE
            payload = self._pending_chunks_buffer[:batch_size]
            self._pending_chunks_buffer = self._pending_chunks_buffer[batch_size:]

            if self._pending_chunks_buffer:
                interval = (
                    0 if self._pending_finish_event else self._STREAM_FLUSH_INTERVAL_MS
                )
                self._flush_timer.start(interval)
            else:
                self._flush_timer.stop()

            self.batchReady.emit(payload, False)

            for row in payload:
                rel = row.get("rel")
                if rel:
                    self._pending_rels.discard(normalise_rel_value(rel))
                abs_val = row.get("abs")
                if abs_val:
                    self._pending_abs.discard(str(abs_val))

            if self._pending_finish_event and not self._pending_chunks_buffer:
                self._finalize_loading(*self._pending_finish_event)

        finally:
            self._is_flushing = False

    def _on_live_chunk_ready(
        self, root: Path, chunk: List[Dict[str, object]]
    ) -> None:
        """Handle chunks from the LiveIngestWorker by routing through K-Way stream.
        
        Live scanner results are pushed to the live_queue of the MergedAssetStream,
        where they will be merged with DB results in chronological order.
        """
        if self._ignore_incoming_chunks:
            return

        if (
            not self._album_root
            or root != self._album_root
            or not chunk
        ):
            return

        # Deduplicate and push to K-Way merge stream
        unique_entries = []
        for row in chunk:
            rel = row.get("rel")
            if not rel:
                continue
            norm_rel = normalise_rel_value(rel)
            abs_val = row.get("abs")
            abs_key = str(abs_val) if abs_val else None

            # Check if already in model
            if self._duplication_checker(norm_rel, abs_key):
                continue
            # Check if already in K-Way stream
            if self._k_way_stream.is_row_tracked(norm_rel, abs_key):
                continue

            unique_entries.append(row)

        if unique_entries:
            # Push to K-Way merge stream's live queue
            added = self._k_way_stream.push_live_chunk(unique_entries)
            logger.debug(
                "Live chunk received: %d entries, %d unique added to stream",
                len(chunk),
                added,
            )
            # Live data is now in the K-Way stream and will be merged
            # with DB data when the UI triggers fetchMore/load_next_page

    def _on_scan_chunk_ready(self, root: Path, chunk: List[Dict[str, object]]) -> None:
        """Process scan results by routing them through the K-Way merge stream.
        
        Live scanner results are pushed to the live_queue of the MergedAssetStream,
        where they will be merged with DB results in chronological order.
        """
        if not self._album_root or not chunk:
            return

        manifest = (
            self._facade.current_album.manifest if self._facade.current_album else {}
        )
        featured = manifest.get("featured", []) or []

        try:
            scan_root = root.resolve()
            view_root = self._album_root.resolve()
        except OSError as exc:
            logger.warning(
                "Failed to resolve paths during scan chunk processing: %s", exc
            )
            return

        is_direct_match = scan_root == view_root
        is_scan_parent_of_view = scan_root in view_root.parents

        if not (is_direct_match or is_scan_parent_of_view):
            return

        featured_set = normalize_featured(featured or [])
        entries: List[Dict[str, object]] = []

        for row in chunk:
            raw_rel = row.get("rel")
            if not raw_rel:
                continue

            full_path = scan_root / raw_rel
            try:
                view_rel = full_path.relative_to(view_root).as_posix()
            except ValueError:
                continue
            except OSError as exc:
                logger.error(
                    "OSError while checking if %s is relative to %s: %s",
                    full_path,
                    view_root,
                    exc,
                )
                continue

            # Check if exists in model or K-Way stream
            norm_rel = normalise_rel_value(view_rel)
            if self._duplication_checker(norm_rel, None):
                continue
            if self._k_way_stream.is_row_tracked(norm_rel):
                continue

            adjusted_row = row.copy()
            adjusted_row["rel"] = view_rel

            entry = build_asset_entry(
                view_root,
                adjusted_row,
                featured_set,
                path_exists=Path.exists,
            )
            if entry is None:
                continue

            if self._active_filter == "videos" and not entry.get("is_video"):
                continue
            if self._active_filter == "live" and not entry.get("is_live"):
                continue
            if self._active_filter == "favorites" and not entry.get("featured"):
                continue

            entries.append(entry)

        if entries:
            # Push live scan results to the K-Way merge stream
            added = self._k_way_stream.push_live_chunk(entries)
            logger.debug(
                "Live scan chunk: %d entries processed, %d unique added to stream",
                len(entries),
                added,
            )
            
            # For live items that are newer than current view position,
            # emit them immediately so they appear at the top
            if added > 0 and not self._is_first_chunk:
                # Only emit live items directly after initial load has started
                # Check if live items are newer than the last rendered DB item
                live_head = self._k_way_stream.peek_live_head()
                if live_head:
                    self.batchReady.emit(entries, False)

    def _on_loader_progress(self, root: Path, current: int, total: int) -> None:
        if not self._album_root or root != self._album_root:
            return
        self.loadProgress.emit(root, current, total)

    def _on_loader_finished(self, root: Path, success: bool) -> None:
        if self._ignore_incoming_chunks:
            self._ignore_incoming_chunks = False
            self._pending_loader_root = None
            self._reset_buffers()
            self.loadFinished.emit(root, success)

            if self._reload_pending:
                self._reload_pending = False
                QTimer.singleShot(0, self.start_load)
            return

        if not self._album_root or root != self._album_root:
            # Stale load
            if self._reload_pending:
                self._reload_pending = False
                QTimer.singleShot(0, self.start_load)
            return

        if not self._pending_chunks_buffer:
            self._finalize_loading(root, success)
        else:
            self._pending_finish_event = (root, success)
            self._flush_pending_chunks()

        # In case we finished a load but a reload was queued during it (though start_load normally cancels)
        # This handles edge cases where running was False but we queued somehow?
        # Mostly defensive, but `start_load` sets `_reload_pending` ONLY if running.
        # If we finished naturally, `_reload_pending` should be False, unless set externally.

    def _finalize_loading(self, root: Path, success: bool) -> None:
        self._pending_finish_event = None
        self._flush_timer.stop()
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
                "Controller: applying deferred incremental refresh for %s.",
                self._album_root,
            )
            self._set_deferred_incremental_refresh(None)
            self.refresh_rows_from_index(self._album_root)

    def _on_loader_error(self, root: Path, message: str) -> None:
        if not self._album_root or root != self._album_root:
            self.loadFinished.emit(root, False)
            if self._reload_pending:
                self._reload_pending = False
                QTimer.singleShot(0, self.start_load)
            return

        self.error.emit(root, message)
        self.loadFinished.emit(root, False)
        self._reset_buffers()
        self._pending_loader_root = None

        if self._reload_pending:
            self._reload_pending = False
            QTimer.singleShot(0, self.start_load)

    def handle_links_updated(
        self, root: Path, current_album_root: Optional[Path]
    ) -> bool:
        if not current_album_root:
            return False

        album_root = normalise_for_compare(current_album_root)
        updated_root = normalise_for_compare(Path(root))

        if not self._links_update_targets_current_view(album_root, updated_root):
            return False

        descendant_root = updated_root if updated_root != album_root else None

        if self._data_loader.is_running() or self._pending_loader_root:
            self._set_deferred_incremental_refresh(current_album_root)
            return True

        self._set_deferred_incremental_refresh(None)
        self.refresh_rows_from_index(
            current_album_root, descendant_root=descendant_root
        )
        return True

    def _links_update_targets_current_view(
        self, album_root: Path, updated_root: Path
    ) -> bool:
        if album_root == updated_root:
            return True
        return is_descendant_path(updated_root, album_root)

    def refresh_rows_from_index(
        self, root: Path, descendant_root: Optional[Path] = None
    ) -> None:
        with QMutexLocker(self._refresh_lock):
            if self._incremental_worker is not None:
                logger.debug(
                    "Controller: incremental refresh already in progress, skipping."
                )
                return

            manifest = (
                self._facade.current_album.manifest
                if self._facade.current_album
                else {}
            )
            featured = manifest.get("featured", []) or []

            filter_params = self._get_filter_params()

            # Get library root for global database filtering
            library_root = None
            if self._facade.library_manager:
                library_root = self._facade.library_manager.root()

            self._incremental_signals = IncrementalRefreshSignals()
            self._incremental_signals.resultsReady.connect(
                self._apply_incremental_results
            )
            self._incremental_signals.error.connect(self._on_incremental_error)

            self._incremental_worker = IncrementalRefreshWorker(
                root,
                featured,
                self._incremental_signals,
                filter_params=filter_params,
                descendant_root=descendant_root,
                library_root=library_root,
            )

            QThreadPool.globalInstance().start(self._incremental_worker)

    def _on_incremental_error(self, root: Path, message: str) -> None:
        logger.error("Incremental refresh error for %s: %s", root, message)
        self._cleanup_incremental_worker()

    def _cleanup_incremental_worker(self) -> None:
        with QMutexLocker(self._refresh_lock):
            if self._incremental_signals:
                try:
                    self._incremental_signals.resultsReady.disconnect(
                        self._apply_incremental_results
                    )
                    self._incremental_signals.error.disconnect(
                        self._on_incremental_error
                    )
                except RuntimeError:
                    # Signals may already be disconnected or the underlying QObject
                    # may have been deleted; ignore disconnect errors during cleanup.
                    logger.debug(
                        "Failed to disconnect incremental refresh signals during cleanup",
                        exc_info=True,
                    )
                self._incremental_signals.deleteLater()
                self._incremental_signals = None
            self._incremental_worker = None

    def _apply_incremental_results(
        self, root: Path, fresh_rows: List[Dict[str, object]]
    ) -> None:
        if not self._album_root or root != self._album_root:
            self._cleanup_incremental_worker()
            return

        self._cleanup_incremental_worker()
        self.incrementalReady.emit(fresh_rows, root)

    def _set_deferred_incremental_refresh(self, root: Optional[Path]) -> None:
        if root is None:
            self._deferred_incremental_refresh = None
            return
        self._deferred_incremental_refresh = normalise_for_compare(root)

    # ------------------------------------------------------------------
    # Cursor-Based Pagination Methods
    # ------------------------------------------------------------------

    def can_load_more(self) -> bool:
        """Return True if more data can be loaded via pagination.
        
        Returns False if:
        - All data has been loaded and K-Way stream is empty
        - A page load is currently in progress
        - No album root is set
        """
        # Check if K-Way stream has buffered data OR DB has more pages
        has_buffered_data = self._k_way_stream.has_data()
        can_fetch_db = not self._all_data_loaded
        
        return (
            (has_buffered_data or can_fetch_db)
            and not self._is_loading_page
            and self._album_root is not None
        )

    def is_loading_page(self) -> bool:
        """Return True if a page load is currently in progress."""
        return self._is_loading_page

    def all_data_loaded(self) -> bool:
        """Return True if all data has been loaded (no more pages)."""
        return self._all_data_loaded and not self._k_way_stream.has_data()

    def load_next_page(self, page_size: int = DEFAULT_PAGE_SIZE) -> bool:
        """Load the next page of assets using the K-Way merge stream.
        
        This method implements lazy pull-based loading:
        1. First, try to fulfill the request from the MergedAssetStream buffer
        2. If buffer has enough data, emit immediately without DB fetch
        3. If buffer is low, trigger a DB page fetch and wait for results
        
        Args:
            page_size: Number of assets to fetch (default: DEFAULT_PAGE_SIZE).
        
        Returns:
            True if data was emitted or a fetch was started, False otherwise.
        """
        if self._album_root is None:
            logger.debug("load_next_page: no album root set")
            return False

        if self._is_loading_page:
            logger.debug("load_next_page: already loading a page")
            return False

        # Step 1: Check if we can fulfill from the K-Way stream buffer
        buffered_count = self._k_way_stream.total_pending()
        
        if buffered_count >= page_size:
            # We have enough buffered data - emit directly without DB fetch
            batch = self._k_way_stream.pop_next(page_size)
            if batch:
                self._emit_batch_from_stream(batch)
                logger.debug(
                    "load_next_page: fulfilled from buffer (%d items, %d remaining)",
                    len(batch),
                    self._k_way_stream.total_pending(),
                )
                return True

        # Step 2: If buffer is low AND DB has more data, fetch from DB
        if not self._all_data_loaded:
            # Calculate how much to fetch (prefetch more than requested for efficiency)
            fetch_size = page_size * self._PREFETCH_MULTIPLIER
            self._trigger_db_page_fetch(fetch_size)
            return True

        # Step 3: If DB is exhausted but we have remaining buffered data, emit it
        if buffered_count > 0:
            batch = self._k_way_stream.pop_next(page_size)
            if batch:
                self._emit_batch_from_stream(batch)
                logger.debug(
                    "load_next_page: drained remaining buffer (%d items)",
                    len(batch),
                )
                return True

        # No data available and DB is exhausted
        if not self._all_data_loaded:
            self._all_data_loaded = True
            self.allDataLoaded.emit()
        logger.debug("load_next_page: all data exhausted")
        return False

    def _trigger_db_page_fetch(self, page_size: int) -> None:
        """Trigger an async fetch of the next DB page.
        
        Args:
            page_size: Number of rows to fetch from the database.
        """
        # Cleanup any existing paginated worker
        self._cleanup_paginated_worker()

        manifest = (
            self._facade.current_album.manifest if self._facade.current_album else {}
        )
        featured = manifest.get("featured", []) or []

        filter_params = self._get_filter_params()

        # Get library root for global database filtering
        library_root = None
        if self._facade.library_manager:
            library_root = self._facade.library_manager.root()

        # Create and connect signals
        self._paginated_signals = PaginatedLoaderSignals(self)
        self._paginated_signals.pageReady.connect(self._on_paginated_page_ready)
        self._paginated_signals.endOfData.connect(self._on_paginated_end_of_data)
        self._paginated_signals.error.connect(self._on_paginated_error)

        # Create and start worker
        self._paginated_worker = PaginatedLoaderWorker(
            self._album_root,
            featured,
            self._paginated_signals,
            filter_params=filter_params,
            library_root=library_root,
            cursor_dt=self._cursor_dt,
            cursor_id=self._cursor_id,
            page_size=page_size,
        )

        self._is_loading_page = True
        QThreadPool.globalInstance().start(self._paginated_worker)

        logger.debug(
            "load_next_page: triggered DB fetch cursor_dt=%s cursor_id=%s page_size=%d",
            self._cursor_dt,
            self._cursor_id,
            page_size,
        )

    def _emit_batch_from_stream(self, batch: List[Dict[str, object]]) -> None:
        """Emit a batch of rows from the K-Way stream to the UI.
        
        Args:
            batch: List of asset dictionaries to emit.
        """
        # Filter out any duplicates that may already be in the model
        unique_batch = []
        for row in batch:
            rel = row.get("rel")
            if not rel:
                continue
            norm_rel = normalise_rel_value(rel)
            abs_val = row.get("abs")
            abs_key = str(abs_val) if abs_val else None

            if not self._duplication_checker(norm_rel, abs_key):
                unique_batch.append(row)

        if unique_batch:
            # Emit as non-reset batch (append mode)
            is_first = self._is_first_chunk
            if is_first:
                self._is_first_chunk = False
            self.batchReady.emit(unique_batch, is_first)

    def _on_paginated_page_ready(
        self,
        root: Path,
        chunk: List[Dict[str, object]],
        last_dt: str,
        last_id: str,
    ) -> None:
        """Handle a page of results from the paginated worker.
        
        In the lazy loading mode, we push data to the K-Way stream
        and then pull merged results to emit to the UI.
        """
        if not self._album_root or root != self._album_root:
            logger.debug("Ignoring paginated page ready for stale album: %s", root)
            self._is_loading_page = False
            return

        # Update cursor for next page
        if last_dt:
            self._cursor_dt = last_dt
        if last_id:
            self._cursor_id = last_id

        # Push to K-Way merge stream (deduplication happens inside)
        added = self._k_way_stream.push_db_chunk(chunk)
        logger.debug(
            "Paginated page ready: %d rows received, %d unique added to stream",
            len(chunk),
            added,
        )

        self._is_loading_page = False

        # Now emit merged data from the stream
        # We pull at least one batch to satisfy the pending UI request
        page_size = DEFAULT_PAGE_SIZE
        batch = self._k_way_stream.pop_next(page_size)
        if batch:
            self._emit_batch_from_stream(batch)
        
        # Check if this is the first page (initial page loaded)
        if not self._initial_page_loaded and batch:
            self._initial_page_loaded = True
            self.initialPageLoaded.emit(root)
            logger.info(
                "Initial page loaded for %s: %d items (first screen ready)",
                root.name,
                len(batch),
            )
            # Start background prefetch after a delay
            self._schedule_background_prefetch()

    def _on_paginated_end_of_data(self, root: Path) -> None:
        """Handle end of data signal from paginated worker."""
        if not self._album_root or root != self._album_root:
            self._is_loading_page = False
            return

        self._all_data_loaded = True
        self._k_way_stream.mark_db_exhausted()
        self._is_loading_page = False
        
        logger.debug("Pagination: DB stream exhausted for album %s", root)
        
        # If there's remaining data in the stream, don't emit allDataLoaded yet
        if not self._k_way_stream.has_data():
            self.allDataLoaded.emit()
            logger.debug("Pagination: all data loaded for album %s", root)

    def _on_paginated_error(self, root: Path, message: str) -> None:
        """Handle error from paginated worker."""
        self._is_loading_page = False
        logger.error("Pagination error for %s: %s", root, message)
        self.error.emit(root, message)

    # ------------------------------------------------------------------
    # Background Prefetch Methods
    # ------------------------------------------------------------------

    def _schedule_background_prefetch(self) -> None:
        """Schedule background prefetch to run after a short delay.
        
        This method schedules the prefetch to run after PREFETCH_DELAY_MS,
        allowing the UI to settle after the initial page load.
        
        Reuses a single timer instance to avoid memory leaks.
        """
        if self._all_data_loaded:
            logger.debug("Skipping prefetch scheduling: all data already loaded")
            return
        
        # Lazily create the prefetch timer once and reuse it
        if self._prefetch_timer is None:
            self._prefetch_timer = QTimer(self)
            self._prefetch_timer.setSingleShot(True)
            self._prefetch_timer.timeout.connect(self._start_background_prefetch)
        else:
            # Ensure any pending timeout is cancelled before rescheduling
            self._prefetch_timer.stop()
        
        # Start (or restart) the prefetch timer
        self._prefetch_timer.start(self.PREFETCH_DELAY_MS)
        
        logger.debug(
            "Background prefetch scheduled in %d ms",
            self.PREFETCH_DELAY_MS,
        )

    def _start_background_prefetch(self) -> None:
        """Start background prefetching of additional pages.
        
        This method prefetches PREFETCH_PAGES pages in the background
        to reduce latency when the user scrolls down.
        """
        if not self._initial_page_loaded:
            logger.debug("Skipping background prefetch: initial page not yet loaded")
            return
        
        if self._all_data_loaded:
            logger.debug("Skipping background prefetch: all data already loaded")
            return
        
        logger.info(
            "Starting background prefetch: %d pages",
            self.PREFETCH_PAGES,
        )
        
        # Use a counter to track prefetch pages
        self._prefetch_remaining = self.PREFETCH_PAGES
        self._prefetch_next_page()

    def _prefetch_next_page(self) -> None:
        """Prefetch the next page of data in the background.
        
        This method schedules recursive calls to prefetch multiple pages.
        Uses a tracking flag to allow cancellation during cleanup.
        """
        # Clear the pending flag since we're now executing
        self._prefetch_pending = False
        
        if self._all_data_loaded or self._is_loading_page:
            return
        
        if self._prefetch_remaining <= 0:
            return
        
        self._prefetch_remaining -= 1
        
        # Trigger a page load - the result will be pushed to K-Way stream
        # and made available when the user scrolls
        success = self.load_next_page()
        
        if success and self._prefetch_remaining > 0:
            # Schedule the next prefetch after a short delay
            # Set flag so we can cancel if album changes
            self._prefetch_pending = True
            QTimer.singleShot(200, self._on_prefetch_timer_fired)
    
    def _on_prefetch_timer_fired(self) -> None:
        """Handle singleShot timer callback for recursive prefetch.
        
        Only proceeds if prefetch is still pending (not cancelled).
        """
        if self._prefetch_pending:
            self._prefetch_next_page()
