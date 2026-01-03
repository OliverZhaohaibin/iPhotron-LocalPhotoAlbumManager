"""Controller for managing asset data loading, streaming, and incremental refreshes."""

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
    build_asset_entry,
    normalize_featured,
)
from ...tasks.incremental_refresh_worker import (
    IncrementalRefreshSignals,
    IncrementalRefreshWorker,
)
from ..asset_data_loader import AssetDataLoader
from .....utils.pathutils import (
    normalise_for_compare,
    is_descendant_path,
    normalise_rel_value,
)

logger = logging.getLogger(__name__)


class AssetListController(QObject):
    """
    Manages data loading, buffering, and background tasks for the asset list.

    Delegates specific loading tasks to `AssetDataLoader`, `LiveIngestWorker`,
    and `IncrementalRefreshWorker`. Handles buffering of incoming chunks to
    prevent UI freezing.
    """

    # Signals
    # Emits (chunk, is_reset)
    batchReady = Signal(list, bool)
    # Emits (fresh_rows, root)
    incrementalReady = Signal(list, Path)
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)
    error = Signal(Path, str)

    # Tuning constants for streaming updates
    _STREAM_FLUSH_INTERVAL_MS = 100
    _STREAM_BATCH_SIZE = 100
    _STREAM_FLUSH_THRESHOLD = 2000

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

        # Workers
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

        # Streaming buffer state
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
            self._live_signals.deleteLater()
            self._live_signals = None

        # Cleanup incremental refresh worker
        self._cleanup_incremental_worker()

        # When preparing for a new album, we should drop any pending reloads for the old one.
        self._reload_pending = False

        self._album_root = root
        self._set_deferred_incremental_refresh(None)
        self._reset_buffers()
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

    def set_filter_mode(self, mode: Optional[str]) -> None:
        """Update filter mode and trigger reload if changed."""
        normalized = mode.casefold() if isinstance(mode, str) and mode else None
        if normalized == self._active_filter:
            return

        self._active_filter = normalized
        self.start_load()

    def active_filter_mode(self) -> Optional[str]:
        return self._active_filter

    def start_load(self) -> None:
        """Start loading assets for the current album root."""
        if not self._album_root:
            return

        if self._data_loader.is_running():
            self._reload_pending = True
            self._data_loader.cancel()
            self._ignore_incoming_chunks = True
            self._reset_buffers()
            self._is_first_chunk = True
            return

        self._reload_pending = False
        self._reset_buffers()
        self._is_first_chunk = True

        manifest = (
            self._facade.current_album.manifest if self._facade.current_album else {}
        )
        featured = manifest.get("featured", []) or []

        self._pending_loader_root = self._album_root
        filter_params = {}
        if self._active_filter:
            filter_params["filter_mode"] = self._active_filter

        try:
            self._data_loader.start(
                self._album_root, featured, filter_params=filter_params
            )
            self._ignore_incoming_chunks = False

            # Live Ingest
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
                        self._live_signals.deleteLater()
                        self._live_signals = None

                    live_items = self._facade.library_manager.get_live_scan_results(
                        relative_to=self._album_root
                    )
                    if live_items:
                        live_signals = AssetLoaderSignals(self)
                        live_signals.chunkReady.connect(self._on_loader_chunk_ready)
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

        except RuntimeError:
            self._pending_loader_root = None
            return

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

    def _on_scan_chunk_ready(self, root: Path, chunk: List[Dict[str, object]]) -> None:
        """Process scan results directly without buffering, as they are live updates."""
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

            # Check if exists in model
            if self._duplication_checker(normalise_rel_value(view_rel), None):
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

            filter_params = {}
            if self._active_filter:
                filter_params["filter_mode"] = self._active_filter

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
