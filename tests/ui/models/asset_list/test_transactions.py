"""Unit tests for OptimisticTransactionManager."""
from __future__ import annotations

from pathlib import Path

from src.iPhoto.gui.ui.models.asset_list.transactions import OptimisticTransactionManager


def test_transaction_manager_no_pending():
    """Test initial state with no pending operations."""
    manager = OptimisticTransactionManager()
    
    assert not manager.has_pending_moves()
    assert not manager.has_pending_removals()


def test_transaction_manager_register_move():
    """Test registering a move operation."""
    manager = OptimisticTransactionManager()
    
    rows = [
        {"rel": "a.jpg", "abs": "/source/a.jpg"},
        {"rel": "b.jpg", "abs": "/source/b.jpg"},
    ]
    row_lookup = {"a.jpg": 0, "b.jpg": 1}
    
    source_root = Path("/source")
    dest_root = Path("/dest")
    
    changed = manager.register_move(
        ["a.jpg"],
        dest_root,
        source_root,
        rows,
        row_lookup,
        is_source_main_view=True,
    )
    
    assert len(changed) == 1
    assert changed[0] == 0
    assert manager.has_pending_moves()
    assert rows[0].get("_pending_move") is True


def test_transaction_manager_finalize_move():
    """Test finalizing a successful move."""
    manager = OptimisticTransactionManager()
    
    rows = [{"rel": "a.jpg", "abs": "/source/a.jpg", "_pending_move": True}]
    row_lookup = {"a.jpg": 0}
    
    album_root = Path("/dest")
    moves = [(Path("/source/a.jpg"), Path("/dest/moved/a.jpg"))]
    
    updated = manager.finalize_moves(moves, rows, row_lookup, album_root)
    
    assert len(updated) == 1
    assert "_pending_move" not in rows[0]
    # Note: The rel won't update in this test because the source is not under album_root


def test_transaction_manager_rollback_move():
    """Test rolling back a failed move."""
    manager = OptimisticTransactionManager()
    
    original_row = {"rel": "a.jpg", "abs": "/source/a.jpg"}
    rows = [original_row.copy()]
    rows[0]["_pending_move"] = True
    rows[0]["abs"] = "/temp/a.jpg"  # Simulate temporary change
    
    row_lookup = {"a.jpg": 0}
    
    # Register the move first
    manager._pending_moves["a.jpg"] = original_row.copy()
    
    restored = manager.rollback_moves(rows, row_lookup, Path("/source"))
    
    assert len(restored) == 1
    assert rows[0]["abs"] == "/source/a.jpg"
    assert "_pending_move" not in rows[0]
    assert not manager.has_pending_moves()


def test_transaction_manager_register_removal():
    """Test registering asset removals."""
    manager = OptimisticTransactionManager()
    
    manager.register_removal(["a.jpg", "b.jpg"])
    
    assert manager.has_pending_removals()


def test_transaction_manager_clear_all():
    """Test clearing all transaction state."""
    manager = OptimisticTransactionManager()
    
    # Set up some state
    manager.register_removal(["a.jpg"])
    manager._pending_moves["b.jpg"] = {"rel": "b.jpg"}
    
    assert manager.has_pending_moves()
    assert manager.has_pending_removals()
    
    manager.clear_all()
    
    assert not manager.has_pending_moves()
    assert not manager.has_pending_removals()
