"""Streaming buffer for efficient chunk processing in asset loading.

This module extracts the buffering and throttling logic from AssetListModel
to keep the UI responsive during large data loads. It also provides a K-Way
Merge implementation for combining ordered data streams from DB and Live
Scanner sources without re-sorting.
"""
from __future__ import annotations

import heapq
import logging
from collections import deque
from typing import Callable, Deque, Dict, Iterator, List, Optional, Set, Tuple

from PySide6.QtCore import QTimer

from .....utils.pathutils import normalise_rel_value

logger = logging.getLogger(__name__)


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


def _get_sort_key(row: Dict[str, object]) -> Tuple[float, str]:
    """Extract a sort key (dt_sort, id) from an asset row for comparison.
    
    Returns a tuple suitable for comparison where higher dt_sort values
    come first (descending order).
    
    Args:
        row: Asset dictionary with 'dt_sort' and 'id' keys.
        
    Returns:
        Tuple of (-dt_sort, id) for descending date order.
    """
    dt_sort = row.get("dt_sort")
    if dt_sort is None:
        dt_sort = float("-inf")
    # Negate for descending order in min-heap
    return (-float(dt_sort), str(row.get("id", "")))


class MergedAssetStream:
    """K-Way Merge implementation for combining DB and Live Scanner streams.
    
    This class implements an efficient O(N) merge of two ordered data streams:
    1. **db_queue**: Data from the paginated database loader (already sorted by date DESC)
    2. **live_queue**: Data from the live scanner (may arrive out of order)
    
    The merge algorithm maintains a priority queue (min-heap) to efficiently
    find the next item to yield, ensuring:
    - O(log K) per item where K is the number of streams (typically 2)
    - O(N) total time complexity (linear in the number of items)
    - No re-sorting of the entire list on each chunk arrival
    
    Usage:
        stream = MergedAssetStream()
        
        # Push data from workers
        stream.push_db_chunk(db_rows)
        stream.push_live_chunk(live_rows)
        
        # Pull merged data for the UI
        batch = stream.pop_next(100)
    """
    
    # Source identifiers for tracking where items came from
    SOURCE_DB = "db"
    SOURCE_LIVE = "live"
    
    def __init__(self) -> None:
        """Initialize the merged stream with empty queues."""
        # DB queue: items arrive in order, use deque for O(1) popleft
        self._db_queue: Deque[Dict[str, object]] = deque()
        
        # Live queue: items may arrive out of order, use a heap for sorting
        # Each entry is (sort_key, insertion_order, row) for stable sorting
        self._live_heap: List[Tuple[Tuple[float, str], int, Dict[str, object]]] = []
        self._live_insertion_counter: int = 0
        
        # Deduplication tracking
        self._seen_rels: Set[str] = set()
        self._seen_abs: Set[str] = set()
        
        # State flags
        self._db_exhausted: bool = False
        self._live_exhausted: bool = False

    def reset(self) -> None:
        """Clear all state and queues."""
        self._db_queue.clear()
        self._live_heap.clear()
        self._live_insertion_counter = 0
        self._seen_rels.clear()
        self._seen_abs.clear()
        self._db_exhausted = False
        self._live_exhausted = False

    def push_db_chunk(self, rows: List[Dict[str, object]]) -> int:
        """Push a chunk of rows from the database stream.
        
        DB rows are expected to arrive in date descending order, so they
        are simply appended to a FIFO queue.
        
        Args:
            rows: List of asset dictionaries from the DB loader.
            
        Returns:
            Number of unique rows added (after deduplication).
        """
        added = 0
        for row in rows:
            if self._add_if_unique(row):
                self._db_queue.append(row)
                added += 1
        return added

    def push_live_chunk(self, rows: List[Dict[str, object]]) -> int:
        """Push a chunk of rows from the live scanner stream.
        
        Live rows may arrive out of order, so they are added to a heap
        for efficient sorted retrieval.
        
        Args:
            rows: List of asset dictionaries from the live scanner.
            
        Returns:
            Number of unique rows added (after deduplication).
        """
        added = 0
        for row in rows:
            if self._add_if_unique(row):
                sort_key = _get_sort_key(row)
                heapq.heappush(
                    self._live_heap,
                    (sort_key, self._live_insertion_counter, row)
                )
                self._live_insertion_counter += 1
                added += 1
        return added

    def _add_if_unique(self, row: Dict[str, object]) -> bool:
        """Check if a row is unique and track it if so.
        
        Returns:
            True if the row was unique and added to tracking sets.
        """
        rel = row.get("rel")
        if not rel:
            return False
            
        norm_rel = normalise_rel_value(rel)
        if norm_rel in self._seen_rels:
            return False
            
        abs_val = row.get("abs")
        abs_key = str(abs_val) if abs_val else None
        if abs_key and abs_key in self._seen_abs:
            return False
            
        self._seen_rels.add(norm_rel)
        if abs_key:
            self._seen_abs.add(abs_key)
        return True

    def mark_db_exhausted(self) -> None:
        """Mark that no more DB data will arrive."""
        self._db_exhausted = True

    def mark_live_exhausted(self) -> None:
        """Mark that no more Live scanner data will arrive."""
        self._live_exhausted = True

    def is_db_exhausted(self) -> bool:
        """Return True if DB stream is exhausted."""
        return self._db_exhausted

    def is_live_exhausted(self) -> bool:
        """Return True if Live scanner stream is exhausted."""
        return self._live_exhausted

    def is_all_exhausted(self) -> bool:
        """Return True if both streams are exhausted."""
        return self._db_exhausted and self._live_exhausted

    def has_data(self) -> bool:
        """Return True if there is data available to pop."""
        return bool(self._db_queue) or bool(self._live_heap)

    def pop_next(self, batch_size: int) -> List[Dict[str, object]]:
        """Pop the next batch of merged items in sorted order.
        
        This method performs a K-way merge between the DB and Live streams,
        yielding items in date descending order.
        
        Algorithm:
        - Compare the head of DB queue with the top of Live heap
        - Yield the item with the higher date (more recent)
        - Repeat until batch_size items are collected or queues are empty
        
        Args:
            batch_size: Maximum number of items to return.
            
        Returns:
            List of asset dictionaries in date descending order.
        """
        result: List[Dict[str, object]] = []
        
        while len(result) < batch_size:
            # Peek at heads of both queues
            db_head = self._db_queue[0] if self._db_queue else None
            live_head = self._live_heap[0][2] if self._live_heap else None
            
            if db_head is None and live_head is None:
                # Both queues empty
                break
            elif db_head is None:
                # Only live data available
                _, _, row = heapq.heappop(self._live_heap)
                result.append(row)
            elif live_head is None:
                # Only DB data available - use popleft for O(1)
                result.append(self._db_queue.popleft())
            else:
                # Both have data - compare and take the more recent one
                db_key = _get_sort_key(db_head)
                live_key = _get_sort_key(live_head)
                
                # Compare: smaller key = more recent (due to negation)
                if db_key <= live_key:
                    result.append(self._db_queue.popleft())
                else:
                    _, _, row = heapq.heappop(self._live_heap)
                    result.append(row)
        
        return result

    def peek_db_head(self) -> Optional[Dict[str, object]]:
        """Peek at the head of the DB queue without removing it."""
        return self._db_queue[0] if self._db_queue else None

    def peek_live_head(self) -> Optional[Dict[str, object]]:
        """Peek at the top of the Live heap without removing it."""
        return self._live_heap[0][2] if self._live_heap else None

    def db_queue_size(self) -> int:
        """Return the number of items in the DB queue."""
        return len(self._db_queue)

    def live_queue_size(self) -> int:
        """Return the number of items in the Live heap."""
        return len(self._live_heap)

    def total_pending(self) -> int:
        """Return the total number of items pending in both queues."""
        return len(self._db_queue) + len(self._live_heap)

    def is_row_tracked(self, rel: str, abs_path: Optional[str] = None) -> bool:
        """Check if a row with the given rel/abs is already tracked.
        
        Args:
            rel: Relative path to check.
            abs_path: Optional absolute path to check.
            
        Returns:
            True if the row has been seen before.
        """
        norm_rel = normalise_rel_value(rel)
        if norm_rel in self._seen_rels:
            return True
        if abs_path and abs_path in self._seen_abs:
            return True
        return False

    def iter_merged(self, batch_size: int = 100) -> Iterator[List[Dict[str, object]]]:
        """Iterate over merged batches until both queues are empty.
        
        This is a convenience method for consuming all available data
        in batches.
        
        Args:
            batch_size: Size of each yielded batch.
            
        Yields:
            Batches of merged asset dictionaries.
        """
        while self.has_data():
            batch = self.pop_next(batch_size)
            if batch:
                yield batch
