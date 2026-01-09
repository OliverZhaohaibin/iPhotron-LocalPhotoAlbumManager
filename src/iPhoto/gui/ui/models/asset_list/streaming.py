"""Streaming buffer for efficient chunk processing in asset loading.

This module extracts the buffering and throttling logic from AssetListModel
to keep the UI responsive during large data loads.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QTimer

from .....utils.pathutils import normalise_rel_value


class AssetStreamBuffer:
    """Manages buffered chunk processing with throttling for responsive UI.
    
    This class handles:
    - Buffering incoming chunks to reduce UI update frequency
    - Throttling updates via a timer to prevent UI freezing
    - Tracking pending items to avoid duplicates
    - Batch flushing with configurable size limits
    """

    # Tuning constants for streaming updates
    DEFAULT_FLUSH_INTERVAL_MS = 100
    DEFAULT_BATCH_SIZE = 100
    DEFAULT_FLUSH_THRESHOLD = 2000

    def __init__(
        self,
        flush_callback: Callable[[List[Dict[str, object]]], None],
        finish_callback: Optional[Callable[[Tuple], None]] = None,
        parent: Optional[object] = None,
    ):
        """Initialize the stream buffer.
        
        Args:
            flush_callback: Called when a batch of chunks is ready to be committed.
                            Receives a list of row dictionaries.
            finish_callback: Called when loading finishes and buffer is empty.
                           Receives the pending finish event tuple (root, success).
            parent: Parent QObject for the timer.
        """
        self._flush_callback = flush_callback
        self._finish_callback = finish_callback
        
        # Buffer state
        self._pending_chunks_buffer: List[Dict[str, object]] = []
        self._pending_rels: set[str] = set()
        self._pending_abs: set[str] = set()
        
        # Timer for throttled flushing
        self._flush_timer = QTimer(parent)
        self._flush_timer.setInterval(self.DEFAULT_FLUSH_INTERVAL_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._on_timer_flush)
        
        # State flags
        self._is_first_chunk = True
        self._is_flushing = False
        self._pending_finish_event: Optional[Tuple] = None

    def reset(self) -> None:
        """Clear all buffered state."""
        self._pending_chunks_buffer = []
        self._pending_rels.clear()
        self._pending_abs.clear()
        self._flush_timer.stop()
        self._pending_finish_event = None
        self._is_first_chunk = True
        self._is_flushing = False

    def is_first_chunk(self) -> bool:
        """Return True if no chunks have been processed yet."""
        return self._is_first_chunk

    def mark_first_chunk_processed(self) -> None:
        """Mark that the first chunk has been processed."""
        self._is_first_chunk = False

    def add_chunk(
        self,
        chunk: List[Dict[str, object]],
        existing_rels: set[str],
        existing_abs_lookup: Callable[[str], Optional[int]],
    ) -> List[Dict[str, object]]:
        """Add a chunk to the buffer after deduplication.
        
        Args:
            chunk: List of asset dictionaries to add.
            existing_rels: Set of relative paths already in the model.
            existing_abs_lookup: Function to check if an absolute path exists.
        
        Returns:
            List of unique rows that were added to the buffer.
        """
        unique_chunk = []
        for row in chunk:
            rel = row.get("rel")
            if not rel:
                continue
            
            norm_rel = normalise_rel_value(rel)
            abs_val = row.get("abs")
            abs_key = str(abs_val) if abs_val else None
            
            # Only add if not already present in MODEL or PENDING BUFFER
            if (
                norm_rel not in existing_rels
                and norm_rel not in self._pending_rels
                and (not abs_key or (
                    existing_abs_lookup(abs_key) is None
                    and abs_key not in self._pending_abs
                ))
            ):
                unique_chunk.append(row)
                self._pending_rels.add(norm_rel)
                if abs_key:
                    self._pending_abs.add(abs_key)

        if unique_chunk:
            self._pending_chunks_buffer.extend(unique_chunk)
            
            # Trigger immediate flush if buffer exceeds threshold
            if len(self._pending_chunks_buffer) >= self.DEFAULT_FLUSH_THRESHOLD:
                self.flush_now()
            elif not self._flush_timer.isActive():
                self._flush_timer.start()

        return unique_chunk

    def flush_now(self) -> None:
        """Flush buffered chunks immediately."""
        self._on_timer_flush()

    def set_finish_event(self, event: Tuple) -> None:
        """Store a pending finish event to be processed after buffer drains.
        
        Args:
            event: Tuple containing (root, success) or similar finish state.
        """
        self._pending_finish_event = event
        # Drain buffer immediately to avoid stalls
        if self._pending_chunks_buffer:
            self.flush_now()

    def has_pending_finish(self) -> bool:
        """Return True if a finish event is pending."""
        return self._pending_finish_event is not None

    def _on_timer_flush(self) -> None:
        """Handle timer timeout by flushing a batch."""
        if self._is_flushing:
            return
        if not self._pending_chunks_buffer:
            return

        self._is_flushing = True
        try:
            # Take only the first N items to avoid freezing the UI
            batch_size = self.DEFAULT_BATCH_SIZE
            payload = self._pending_chunks_buffer[:batch_size]

            # Leave the rest for the next timer tick
            self._pending_chunks_buffer = self._pending_chunks_buffer[batch_size:]

            # If data remains, ensure the timer continues
            if self._pending_chunks_buffer:
                interval = 0 if self._pending_finish_event else self.DEFAULT_FLUSH_INTERVAL_MS
                self._flush_timer.start(interval)
            else:
                self._flush_timer.stop()

            # Call the flush callback with the batch
            self._flush_callback(payload)

            # Cleanup pending sets for committed items
            for row in payload:
                rel = row.get("rel")
                if rel:
                    self._pending_rels.discard(normalise_rel_value(rel))
                abs_val = row.get("abs")
                if abs_val:
                    self._pending_abs.discard(str(abs_val))

            # If buffer is empty and we have a pending finish, finalize it
            if self._pending_finish_event and not self._pending_chunks_buffer:
                if self._finish_callback:
                    self._finish_callback(self._pending_finish_event)
                self._pending_finish_event = None

        finally:
            self._is_flushing = False

    def is_empty(self) -> bool:
        """Return True if the pending chunks buffer is empty.

        Note: This only reflects the state of ``_pending_chunks_buffer``.
        There may still be pending rel/abs in the tracking sets or a pending
        finish event.
        """
        return len(self._pending_chunks_buffer) == 0
