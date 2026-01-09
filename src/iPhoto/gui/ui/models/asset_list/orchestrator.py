"""Data loading orchestration for asset list model.

This module manages the lifecycle of data workers (AssetDataLoader, LiveIngestWorker,
Scanner) and coordinates their output through filters and streaming buffers.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThreadPool, Signal

from ...tasks.asset_loader_worker import (
    AssetLoaderSignals,
    LiveIngestWorker,
    build_asset_entry,
    normalize_featured,
)
from .filter_engine import ModelFilterHandler
from .streaming import AssetStreamBuffer
from .....utils.pathutils import normalise_rel_value

if TYPE_CHECKING:
    from ...tasks.asset_loader_worker import AssetDataLoader
    from ..asset_state_manager import AssetListStateManager

logger = logging.getLogger(__name__)


class AssetDataOrchestrator(QObject):
    """Orchestrates data loading workers and prepares data for model consumption.
    
    This class acts as a coordinator between:
    - AssetDataLoader (database reads)
    - LiveIngestWorker (scanner output)
    - ModelFilterHandler (filtering logic)
    - AssetStreamBuffer (buffering and throttling)
    
    It handles signal connections, data deduplication, and feeding prepared
    data to the model for insertion.
    """
    
    # Signal emitted when a batch of rows is ready to be inserted into the model
    # Parameters: (start_row: int, rows: List[Dict[str, object]])
    rowsReadyForInsertion = Signal(int, list)
    
    # Signal for first chunk special handling
    # Parameters: (rows: List[Dict[str, object]], should_reset: bool)
    firstChunkReady = Signal(list, bool)
    
    # Forward loader signals
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)
    loadError = Signal(Path, str)
    
    def __init__(
        self,
        data_loader: "AssetDataLoader",
        filter_handler: ModelFilterHandler,
        parent: Optional[QObject] = None,
    ):
        """Initialize the orchestrator.
        
        Args:
            data_loader: The AssetDataLoader instance to manage.
            filter_handler: Filter handler for applying filters to chunks.
            parent: Parent QObject.
        """
        super().__init__(parent)
        self._data_loader = data_loader
        self._filter_handler = filter_handler
        
        # Streaming buffer for throttled updates
        self._stream_buffer = AssetStreamBuffer(
            self._on_batch_ready,
            self._on_finish_event,
            parent=self,
        )
        
        # State tracking
        self._album_root: Optional[Path] = None
        self._pending_loader_root: Optional[Path] = None
        self._ignore_incoming_chunks = False
        self._current_live_worker: Optional[LiveIngestWorker] = None
        
        # Connect data loader signals
        self._data_loader.chunkReady.connect(self._on_loader_chunk_ready)
        self._data_loader.loadProgress.connect(self._on_loader_progress)
        self._data_loader.loadFinished.connect(self._on_loader_finished)
        self._data_loader.error.connect(self._on_loader_error)
    
    def set_album_root(self, root: Optional[Path]) -> None:
        """Set the current album root."""
        self._album_root = root
    
    def start_load(
        self,
        album_root: Path,
        featured: List[str],
        filter_params: Optional[Dict[str, Any]] = None,
        library_manager: Optional[Any] = None,
    ) -> None:
        """Start loading data for the given album.
        
        Args:
            album_root: Root path of the album to load.
            featured: List of featured asset paths.
            filter_params: Optional filter parameters.
            library_manager: Optional library manager for live scan results.
        """
        if self._data_loader.is_running():
            self._data_loader.cancel()
            self._ignore_incoming_chunks = True
            self._stream_buffer.reset()
            return
        
        self._album_root = album_root
        self._pending_loader_root = album_root
        self._ignore_incoming_chunks = False
        self._stream_buffer.reset()
        
        try:
            self._data_loader.start(album_root, featured, filter_params=filter_params)
            
            # Inject live scan results if available
            if library_manager:
                self._inject_live_scan_results(
                    library_manager, album_root, featured, filter_params
                )
        except RuntimeError:
            self._pending_loader_root = None
            raise
    
    def cancel_load(self) -> None:
        """Cancel the current load operation."""
        if self._data_loader.is_running():
            self._data_loader.cancel()
            self._ignore_incoming_chunks = True
            self._stream_buffer.reset()
    
    def is_loading(self) -> bool:
        """Return True if a load operation is in progress."""
        return self._data_loader.is_running()
    
    def _inject_live_scan_results(
        self,
        library_manager: Any,
        album_root: Path,
        featured: List[str],
        filter_params: Optional[Dict[str, Any]],
    ) -> None:
        """Inject live (not yet persisted) scan results into the load stream."""
        try:
            if self._current_live_worker:
                self._current_live_worker.cancel()
                self._current_live_worker = None
            
            live_items = library_manager.get_live_scan_results(relative_to=album_root)
            if not live_items:
                return
            
            live_signals = AssetLoaderSignals(self)
            live_signals.chunkReady.connect(self._on_loader_chunk_ready)
            live_signals.finished.connect(lambda _, __: live_signals.deleteLater())
            
            worker = LiveIngestWorker(
                album_root,
                live_items,
                featured,
                live_signals,
                filter_params=filter_params,
            )
            self._current_live_worker = worker
            QThreadPool.globalInstance().start(worker)
        except Exception as e:
            logger.error("Failed to inject live scan results: %s", e, exc_info=True)
    
    def _on_loader_chunk_ready(
        self,
        root: Path,
        chunk: List[Dict[str, object]],
    ) -> None:
        """Handle a chunk of data from the loader."""
        if self._ignore_incoming_chunks:
            return
        
        if (
            not self._album_root
            or root != self._album_root
            or not chunk
            or self._pending_loader_root != self._album_root
        ):
            return
        
        # Filter chunk if filter is active
        if self._filter_handler.is_active():
            chunk = self._filter_handler.filter_rows(chunk)
            if not chunk:
                return
        
        # Delegate to stream buffer (which will call _on_batch_ready)
        # Note: The buffer needs access to existing rows for deduplication
        # This will be handled by the callback
        if self._stream_buffer.is_first_chunk():
            # Signal the model to decide on reset vs append
            # based on whether it already has rows
            self.firstChunkReady.emit(chunk, True)
            self._stream_buffer.mark_first_chunk_processed()
        else:
            # Add to buffer - deduplication happens in the model
            # for now since it needs access to row_lookup
            # This is a simplification for Phase 1
            # In future, we can improve this
            pass
    
    def add_chunk_to_buffer(
        self,
        chunk: List[Dict[str, object]],
        existing_rels: set[str],
        existing_abs_lookup: Any,
    ) -> None:
        """Add a chunk to the streaming buffer after deduplication."""
        unique_chunk = self._stream_buffer.add_chunk(
            chunk, existing_rels, existing_abs_lookup
        )
    
    def flush_buffer(self) -> None:
        """Flush any pending chunks in the buffer."""
        self._stream_buffer.flush_now()
    
    def _on_batch_ready(self, batch: List[Dict[str, object]]) -> None:
        """Called when a batch is ready from the stream buffer."""
        # Emit signal for model to insert the batch
        # The model will determine start_row
        self.rowsReadyForInsertion.emit(-1, batch)  # -1 means calculate start_row
    
    def _on_finish_event(self, event: tuple) -> None:
        """Called when loading finishes and buffer is empty."""
        root, success = event
        self.loadFinished.emit(root, success)
    
    def _on_loader_progress(self, root: Path, current: int, total: int) -> None:
        """Forward progress signal."""
        if self._album_root and root == self._album_root:
            self.loadProgress.emit(root, current, total)
    
    def _on_loader_finished(self, root: Path, success: bool) -> None:
        """Handle loader completion."""
        if self._ignore_incoming_chunks:
            self._ignore_incoming_chunks = False
            self._pending_loader_root = None
            self._stream_buffer.reset()
            self.loadFinished.emit(root, success)
            return
        
        if not self._album_root or root != self._album_root:
            return
        
        # Store finish event and let buffer drain
        if not self._stream_buffer.is_empty():
            self._stream_buffer.set_finish_event((root, success))
            self._stream_buffer.flush_now()
        else:
            self._pending_loader_root = None
            self.loadFinished.emit(root, success)
    
    def _on_loader_error(self, root: Path, message: str) -> None:
        """Forward error signal."""
        self._stream_buffer.reset()
        self._pending_loader_root = None
        self.loadError.emit(root, message)

    # ------------------------------------------------------------------
    # Live scan integration
    # ------------------------------------------------------------------
    @staticmethod
    def process_scan_chunk(
        root: Path,
        chunk: List[Dict[str, object]],
        album_root: Optional[Path],
        state_manager: "AssetListStateManager",
        *,
        featured: Optional[List[str]] = None,
        filter_mode: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        """Prepare scanner rows for insertion into the live model view.

        This method normalises scanner output relative to the active album root,
        filters out duplicates, and applies the current filter mode before
        returning rows ready for insertion.
        """
        if not album_root or not chunk:
            return []

        try:
            scan_root = root.resolve()
            view_root = album_root.resolve()
        except OSError as exc:
            logger.warning("Failed to resolve paths during scan chunk processing: %s", exc)
            return []

        is_direct_match = scan_root == view_root
        is_scan_parent_of_view = scan_root in view_root.parents

        if not (is_direct_match or is_scan_parent_of_view):
            return []

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

            if normalise_rel_value(view_rel) in state_manager.row_lookup:
                continue

            adjusted_row = row.copy()
            adjusted_row["rel"] = view_rel

            entry = build_asset_entry(view_root, adjusted_row, featured_set)
            if entry is None:
                continue

            if filter_mode == "videos" and not entry.get("is_video"):
                continue
            if filter_mode == "live" and not entry.get("is_live"):
                continue
            if filter_mode == "favorites" and not entry.get("featured"):
                continue

            entries.append(entry)

        return entries
