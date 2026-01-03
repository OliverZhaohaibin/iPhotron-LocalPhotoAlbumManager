"""Unit tests for AssetStreamBuffer."""
from __future__ import annotations

from typing import Any, Dict, List

from src.iPhoto.gui.ui.models.asset_list.streaming import AssetStreamBuffer


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
