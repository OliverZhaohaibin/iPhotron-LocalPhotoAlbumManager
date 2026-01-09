"""Transaction management for optimistic UI updates.

This module handles the "optimistic UI" pattern where operations like moves and
deletes are reflected immediately in the UI while the actual backend operation
is still in progress.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .....utils.pathutils import normalise_rel_value

logger = logging.getLogger(__name__)


class OptimisticTransactionManager:
    """Manages optimistic UI state for move and delete operations.
    
    This class tracks:
    - Pending move operations and their temporary state
    - Rollback information to restore original state on failure
    - Changed row indexes for efficient UI updates
    """

    def __init__(self):
        """Initialize the transaction manager."""
        # Maps rel (relative path) to original row data for rollback
        self._pending_moves: Dict[str, Dict[str, object]] = {}
        
        # Set of rels that have been optimistically removed
        self._pending_removals: Set[str] = set()

    def has_pending_moves(self) -> bool:
        """Return True if there are pending move operations."""
        return bool(self._pending_moves)

    def has_pending_removals(self) -> bool:
        """Return True if there are pending removal operations."""
        return bool(self._pending_removals)

    def register_move(
        self,
        rels: List[str],
        destination_root: Path,
        source_root: Path,
        rows: List[Dict[str, object]],
        row_lookup: Dict[str, int],
        is_source_main_view: bool = False,
    ) -> List[int]:
        """Register optimistic move updates.
        
        Args:
            rels: List of relative paths being moved.
            destination_root: Destination album root.
            source_root: Source album root.
            rows: Current list of asset rows.
            row_lookup: Mapping from rel to row index.
            is_source_main_view: True if moving from the main view.
        
        Returns:
            List of row indexes that were updated.
        """
        changed_rows = []
        
        for rel in rels:
            norm_rel = normalise_rel_value(rel)
            row_index = row_lookup.get(norm_rel)
            
            if row_index is None or not (0 <= row_index < len(rows)):
                continue
            
            row = rows[row_index]
            
            # Save original state for rollback
            if norm_rel not in self._pending_moves:
                self._pending_moves[norm_rel] = row.copy()
            
            # Apply optimistic update
            if is_source_main_view:
                # Mark as pending move placeholder
                row["_pending_move"] = True
            else:
                # Update path to destination
                try:
                    full_src_path = source_root / rel
                    new_rel = full_src_path.relative_to(destination_root)
                    row["rel"] = str(new_rel)
                    row["abs"] = str(full_src_path)
                except (ValueError, OSError) as e:
                    # Path calculations failed, skip update
                    logger.warning(
                        "Failed to calculate new path for move operation: %s -> %s (error: %s)",
                        rel, destination_root, e
                    )
                    continue
            
            changed_rows.append(row_index)
        
        return changed_rows

    def register_removal(self, rels: List[str]) -> None:
        """Register optimistic removal of assets.
        
        Args:
            rels: List of relative paths being removed.
        """
        for rel in rels:
            self._pending_removals.add(normalise_rel_value(rel))

    def finalize_moves(
        self,
        moves: List[Tuple[Path, Path]],
        rows: List[Dict[str, object]],
        row_lookup: Dict[str, int],
        album_root: Optional[Path],
    ) -> List[int]:
        """Reconcile optimistic move updates with actual results.
        
        Args:
            moves: List of (source_path, dest_path) tuples from the worker.
            rows: Current list of asset rows.
            row_lookup: Mapping from rel to row index.
            album_root: Current album root.
        
        Returns:
            List of row indexes that were updated.
        """
        updated_rows = []
        
        if not album_root:
            return updated_rows
        
        for src_path, dest_path in moves:
            try:
                src_rel = src_path.relative_to(album_root)
                norm_rel = normalise_rel_value(str(src_rel))
            except ValueError:
                continue
            
            row_index = row_lookup.get(norm_rel)
            if row_index is None or not (0 <= row_index < len(rows)):
                continue
            
            row = rows[row_index]
            
            # Clear pending move flag
            if "_pending_move" in row:
                del row["_pending_move"]
            
            # Update to final destination path
            try:
                final_rel = dest_path.relative_to(album_root)
                row["rel"] = str(final_rel)
                row["abs"] = str(dest_path)
            except ValueError:
                # Destination is outside current album, keep original
                pass
            
            # Remove from pending moves
            self._pending_moves.pop(norm_rel, None)
            
            updated_rows.append(row_index)
        
        return updated_rows

    def rollback_moves(
        self,
        rows: List[Dict[str, object]],
        row_lookup: Dict[str, int],
        album_root: Optional[Path],
    ) -> List[int]:
        """Restore original state for failed or cancelled moves.
        
        Args:
            rows: Current list of asset rows.
            row_lookup: Mapping from rel to row index.
            album_root: Current album root.
        
        Returns:
            List of row indexes that were restored.
        """
        restored_rows = []
        
        for norm_rel, original_row in list(self._pending_moves.items()):
            row_index = row_lookup.get(norm_rel)
            if row_index is None or not (0 <= row_index < len(rows)):
                continue
            
            # Restore original data
            row = rows[row_index]
            for key, value in original_row.items():
                row[key] = value
            
            # Remove pending move flag
            row.pop("_pending_move", None)
            
            restored_rows.append(row_index)
        
        # Clear pending moves
        self._pending_moves.clear()
        
        return restored_rows

    def clear_pending_moves(self) -> None:
        """Clear all pending move state without rollback."""
        self._pending_moves.clear()

    def clear_pending_removals(self) -> None:
        """Clear all pending removal state."""
        self._pending_removals.clear()

    def clear_all(self) -> None:
        """Clear all pending transaction state."""
        self._pending_moves.clear()
        self._pending_removals.clear()
