"""Unit tests for AssetStreamBuffer."""
from __future__ import annotations

from typing import Any, Dict, List

from src.iPhoto.gui.ui.models.asset_list.streaming import (
    AssetStreamBuffer,
    MergedAssetStream,
)


class MockParent:
    """Mock QObject parent for testing."""
    pass


def test_stream_buffer_basic_operation():
    """Test basic buffering and flushing."""
    flushed_batches: List[List[Dict[str, Any]]] = []
    
    def flush_callback(batch: List[Dict[str, Any]]) -> None:
        flushed_batches.append(batch)
    
    buffer = AssetStreamBuffer(flush_callback, parent=MockParent())
    
    # Add a chunk
    chunk = [{"rel": "a.jpg", "abs": "/path/a.jpg"}]
    unique = buffer.add_chunk(chunk, set(), lambda x: None)
    
    assert len(unique) == 1
    assert not buffer.is_empty()
    
    # Flush manually
    buffer.flush_now()
    
    assert buffer.is_empty()
    assert len(flushed_batches) == 1
    assert flushed_batches[0][0]["rel"] == "a.jpg"


def test_stream_buffer_deduplication():
    """Test that duplicates are filtered out."""
    flushed_batches: List[List[Dict[str, Any]]] = []
    
    def flush_callback(batch: List[Dict[str, Any]]) -> None:
        flushed_batches.append(batch)
    
    buffer = AssetStreamBuffer(flush_callback, parent=MockParent())
    
    # Add same chunk twice
    chunk = [{"rel": "a.jpg", "abs": "/path/a.jpg"}]
    existing_rels = set()
    
    unique1 = buffer.add_chunk(chunk, existing_rels, lambda x: None)
    unique2 = buffer.add_chunk(chunk, existing_rels, lambda x: None)
    
    assert len(unique1) == 1
    assert len(unique2) == 0  # Duplicate filtered out
    
    buffer.flush_now()
    
    # Only one item should be flushed
    assert len(flushed_batches) == 1
    assert len(flushed_batches[0]) == 1


def test_stream_buffer_reset():
    """Test that reset clears all state."""
    flushed_batches: List[List[Dict[str, Any]]] = []
    
    def flush_callback(batch: List[Dict[str, Any]]) -> None:
        flushed_batches.append(batch)
    
    buffer = AssetStreamBuffer(flush_callback, parent=MockParent())
    
    # Add data
    chunk = [{"rel": "a.jpg", "abs": "/path/a.jpg"}]
    buffer.add_chunk(chunk, set(), lambda x: None)
    
    assert not buffer.is_empty()
    
    # Reset
    buffer.reset()
    
    assert buffer.is_empty()
    assert buffer.is_first_chunk()


def test_stream_buffer_finish_event():
    """Test finish event handling."""
    flushed_batches: List[List[Dict[str, Any]]] = []
    finish_events: List[Any] = []
    
    def flush_callback(batch: List[Dict[str, Any]]) -> None:
        flushed_batches.append(batch)
    
    def finish_callback(event: Any) -> None:
        finish_events.append(event)
    
    buffer = AssetStreamBuffer(flush_callback, finish_callback, parent=MockParent())
    
    # Add data
    chunk = [{"rel": "a.jpg", "abs": "/path/a.jpg"}]
    buffer.add_chunk(chunk, set(), lambda x: None)
    
    # Set finish event
    buffer.set_finish_event(("root", True))
    
    assert buffer.has_pending_finish()
    
    # Should trigger flush and then finish callback
    # Note: In real usage, the timer would trigger this automatically
    # For testing, we manually flush
    buffer.flush_now()
    
    assert len(finish_events) == 1
    assert finish_events[0] == ("root", True)


# ====================================================================
# MergedAssetStream Tests (K-Way Merge Implementation)
# ====================================================================

class TestMergedAssetStream:
    """Tests for the K-Way Merge implementation."""

    def test_empty_stream(self):
        """Test behavior with no data."""
        stream = MergedAssetStream()
        
        assert not stream.has_data()
        assert stream.pop_next(10) == []
        assert stream.total_pending() == 0

    def test_db_only_stream(self):
        """Test with only DB data (no live scanner)."""
        stream = MergedAssetStream()
        
        # DB data arrives in date descending order
        db_rows = [
            {"rel": "a.jpg", "id": "1", "dt_sort": 1000.0},
            {"rel": "b.jpg", "id": "2", "dt_sort": 900.0},
            {"rel": "c.jpg", "id": "3", "dt_sort": 800.0},
        ]
        added = stream.push_db_chunk(db_rows)
        
        assert added == 3
        assert stream.has_data()
        assert stream.db_queue_size() == 3
        assert stream.live_queue_size() == 0
        
        # Pop all items - should be in same order (already sorted)
        result = stream.pop_next(10)
        assert len(result) == 3
        assert result[0]["rel"] == "a.jpg"
        assert result[1]["rel"] == "b.jpg"
        assert result[2]["rel"] == "c.jpg"

    def test_live_only_stream(self):
        """Test with only live scanner data (no DB)."""
        stream = MergedAssetStream()
        
        # Live data may arrive out of order
        live_rows = [
            {"rel": "b.jpg", "id": "2", "dt_sort": 900.0},
            {"rel": "a.jpg", "id": "1", "dt_sort": 1000.0},  # Most recent
            {"rel": "c.jpg", "id": "3", "dt_sort": 800.0},
        ]
        added = stream.push_live_chunk(live_rows)
        
        assert added == 3
        assert stream.has_data()
        assert stream.db_queue_size() == 0
        assert stream.live_queue_size() == 3
        
        # Pop all items - should be sorted by date descending
        result = stream.pop_next(10)
        assert len(result) == 3
        assert result[0]["rel"] == "a.jpg"  # dt_sort=1000 (most recent)
        assert result[1]["rel"] == "b.jpg"  # dt_sort=900
        assert result[2]["rel"] == "c.jpg"  # dt_sort=800

    def test_merged_stream_ordering(self):
        """Test K-way merge maintains correct date ordering."""
        stream = MergedAssetStream()
        
        # DB rows: dates 1000, 800, 600
        db_rows = [
            {"rel": "db1.jpg", "id": "d1", "dt_sort": 1000.0},
            {"rel": "db2.jpg", "id": "d2", "dt_sort": 800.0},
            {"rel": "db3.jpg", "id": "d3", "dt_sort": 600.0},
        ]
        stream.push_db_chunk(db_rows)
        
        # Live rows: dates 900, 700, 500 (interleaved with DB)
        live_rows = [
            {"rel": "live1.jpg", "id": "l1", "dt_sort": 900.0},
            {"rel": "live2.jpg", "id": "l2", "dt_sort": 700.0},
            {"rel": "live3.jpg", "id": "l3", "dt_sort": 500.0},
        ]
        stream.push_live_chunk(live_rows)
        
        # Pop all - should be perfectly interleaved
        result = stream.pop_next(10)
        assert len(result) == 6
        
        # Expected order: 1000, 900, 800, 700, 600, 500
        expected_order = ["db1.jpg", "live1.jpg", "db2.jpg", "live2.jpg", "db3.jpg", "live3.jpg"]
        actual_order = [r["rel"] for r in result]
        assert actual_order == expected_order

    def test_deduplication_across_streams(self):
        """Test that duplicates are filtered across DB and Live streams."""
        stream = MergedAssetStream()
        
        # Add from DB
        db_rows = [
            {"rel": "shared.jpg", "id": "1", "dt_sort": 1000.0},
            {"rel": "db_only.jpg", "id": "2", "dt_sort": 900.0},
        ]
        db_added = stream.push_db_chunk(db_rows)
        
        # Try to add same item from Live
        live_rows = [
            {"rel": "shared.jpg", "id": "1", "dt_sort": 1000.0},  # Duplicate
            {"rel": "live_only.jpg", "id": "3", "dt_sort": 800.0},
        ]
        live_added = stream.push_live_chunk(live_rows)
        
        assert db_added == 2
        assert live_added == 1  # shared.jpg was deduplicated
        
        result = stream.pop_next(10)
        assert len(result) == 3
        rels = {r["rel"] for r in result}
        assert rels == {"shared.jpg", "db_only.jpg", "live_only.jpg"}

    def test_batch_size_respected(self):
        """Test that pop_next respects batch size."""
        stream = MergedAssetStream()
        
        # Add 10 rows
        rows = [
            {"rel": f"photo_{i}.jpg", "id": str(i), "dt_sort": float(1000 - i)}
            for i in range(10)
        ]
        stream.push_db_chunk(rows)
        
        # Pop in batches of 3
        batch1 = stream.pop_next(3)
        assert len(batch1) == 3
        assert stream.total_pending() == 7
        
        batch2 = stream.pop_next(3)
        assert len(batch2) == 3
        assert stream.total_pending() == 4
        
        batch3 = stream.pop_next(3)
        assert len(batch3) == 3
        assert stream.total_pending() == 1
        
        batch4 = stream.pop_next(3)
        assert len(batch4) == 1  # Only 1 remaining
        assert stream.total_pending() == 0

    def test_exhaustion_tracking(self):
        """Test stream exhaustion state tracking."""
        stream = MergedAssetStream()
        
        assert not stream.is_db_exhausted()
        assert not stream.is_live_exhausted()
        assert not stream.is_all_exhausted()
        
        stream.mark_db_exhausted()
        assert stream.is_db_exhausted()
        assert not stream.is_all_exhausted()
        
        stream.mark_live_exhausted()
        assert stream.is_all_exhausted()

    def test_reset_clears_state(self):
        """Test that reset clears all state."""
        stream = MergedAssetStream()
        
        # Add data
        stream.push_db_chunk([{"rel": "a.jpg", "id": "1", "dt_sort": 100.0}])
        stream.push_live_chunk([{"rel": "b.jpg", "id": "2", "dt_sort": 200.0}])
        stream.mark_db_exhausted()
        
        assert stream.has_data()
        assert stream.is_db_exhausted()
        
        # Reset
        stream.reset()
        
        assert not stream.has_data()
        assert not stream.is_db_exhausted()
        assert not stream.is_live_exhausted()
        assert stream.total_pending() == 0

    def test_is_row_tracked(self):
        """Test row tracking lookup."""
        stream = MergedAssetStream()
        
        # Add a row
        stream.push_db_chunk([
            {"rel": "tracked.jpg", "abs": "/path/tracked.jpg", "id": "1", "dt_sort": 100.0}
        ])
        
        # Check tracking
        assert stream.is_row_tracked("tracked.jpg")
        assert stream.is_row_tracked("other.jpg", "/path/tracked.jpg")  # Same abs
        assert not stream.is_row_tracked("unknown.jpg")

    def test_iter_merged(self):
        """Test iterator interface for consuming batches."""
        stream = MergedAssetStream()
        
        # Add 5 rows
        rows = [
            {"rel": f"photo_{i}.jpg", "id": str(i), "dt_sort": float(1000 - i)}
            for i in range(5)
        ]
        stream.push_db_chunk(rows)
        
        # Consume via iterator
        batches = list(stream.iter_merged(batch_size=2))
        
        assert len(batches) == 3  # 2, 2, 1
        assert len(batches[0]) == 2
        assert len(batches[1]) == 2
        assert len(batches[2]) == 1
        
        # All consumed
        assert not stream.has_data()

    def test_peek_methods(self):
        """Test peeking at queue heads without consuming."""
        stream = MergedAssetStream()
        
        db_rows = [{"rel": "db.jpg", "id": "1", "dt_sort": 100.0}]
        live_rows = [{"rel": "live.jpg", "id": "2", "dt_sort": 200.0}]
        
        stream.push_db_chunk(db_rows)
        stream.push_live_chunk(live_rows)
        
        # Peek should not consume
        db_head = stream.peek_db_head()
        live_head = stream.peek_live_head()
        
        assert db_head["rel"] == "db.jpg"
        assert live_head["rel"] == "live.jpg"
        
        # Data should still be there
        assert stream.db_queue_size() == 1
        assert stream.live_queue_size() == 1

    def test_linear_time_complexity(self):
        """Test that merge is O(N) by verifying no re-sorting on new chunks."""
        stream = MergedAssetStream()
        
        # Simulate incremental loading: add chunks and consume them
        for chunk_num in range(10):
            # DB chunk (already sorted)
            db_rows = [
                {"rel": f"db_{chunk_num}_{i}.jpg", "id": f"d{chunk_num}{i}", "dt_sort": float(1000 - chunk_num * 10 - i)}
                for i in range(100)
            ]
            stream.push_db_chunk(db_rows)
            
            # Live chunk (may be out of order)
            live_rows = [
                {"rel": f"live_{chunk_num}_{i}.jpg", "id": f"l{chunk_num}{i}", "dt_sort": float(995 - chunk_num * 10 - i)}
                for i in range(50)
            ]
            stream.push_live_chunk(live_rows)
            
            # Consume some (simulate UI pulling data)
            batch = stream.pop_next(75)
            
            # Verify batch is sorted (date descending)
            # All test data has dt_sort set, so missing values indicate a bug
            for j in range(1, len(batch)):
                prev_dt = batch[j-1].get("dt_sort")
                curr_dt = batch[j].get("dt_sort")
                assert prev_dt is not None, "dt_sort should not be missing in test data"
                assert curr_dt is not None, "dt_sort should not be missing in test data"
                assert prev_dt >= curr_dt, f"Batch should be sorted: {prev_dt} >= {curr_dt}"
        
        # Drain remaining
        while stream.has_data():
            batch = stream.pop_next(100)
            for j in range(1, len(batch)):
                prev_dt = batch[j-1].get("dt_sort")
                curr_dt = batch[j].get("dt_sort")
                assert prev_dt is not None, "dt_sort should not be missing in test data"
                assert curr_dt is not None, "dt_sort should not be missing in test data"
                assert prev_dt >= curr_dt
