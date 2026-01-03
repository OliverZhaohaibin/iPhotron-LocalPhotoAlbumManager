"""Filter engine for in-memory asset filtering.

This module provides filtering logic for asset types (videos, favorites, etc.)
without requiring database round-trips.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ModelFilterHandler:
    """Handles filtering of assets by type, status, and other criteria.
    
    This class provides:
    - Filter mode validation
    - In-memory filtering of asset rows
    - Filter parameter extraction for database queries
    """

    # Whitelist of valid filter modes
    VALID_MODES = frozenset({"videos", "live", "favorites"})

    def __init__(self):
        """Initialize the filter handler."""
        self._active_mode: Optional[str] = None

    def set_mode(self, mode: Optional[str]) -> bool:
        """Set the active filter mode.
        
        Args:
            mode: Filter mode string ("videos", "live", "favorites", or None).
        
        Returns:
            True if the mode changed, False if it stayed the same.
        """
        normalized = mode.casefold() if isinstance(mode, str) and mode else None
        
        if normalized == self._active_mode:
            return False
        
        self._active_mode = normalized
        return True

    def get_mode(self) -> Optional[str]:
        """Get the current filter mode."""
        return self._active_mode

    def is_active(self) -> bool:
        """Return True if a filter is currently active."""
        return self._active_mode is not None

    def matches_filter(self, row: Dict[str, Any]) -> bool:
        """Check if a row matches the current filter mode.
        
        Args:
            row: Asset dictionary to check.
        
        Returns:
            True if the row passes the filter (or no filter is active).
        """
        if self._active_mode is None:
            return True
        
        if self._active_mode == "videos":
            return bool(row.get("is_video", False) or row.get("media_type") == 1)
        elif self._active_mode == "live":
            return bool(row.get("is_live", False) or row.get("live_partner_rel"))
        elif self._active_mode == "favorites":
            return bool(row.get("featured", False) or row.get("is_favorite"))
        
        return True

    def filter_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter a list of rows based on the current mode.
        
        Args:
            rows: List of asset dictionaries to filter.
        
        Returns:
            Filtered list of asset dictionaries.
        """
        if self._active_mode is None:
            return rows
        
        return [row for row in rows if self.matches_filter(row)]

    def get_filter_params(self) -> Optional[Dict[str, str]]:
        """Get filter parameters for database queries.
        
        Returns:
            Dictionary with 'filter_mode' key, or None if no filter is active.
        """
        if self._active_mode is None:
            return None
        
        return {"filter_mode": self._active_mode}

    def is_valid_mode(self, mode: Optional[str]) -> bool:
        """Check if a filter mode is valid.
        
        Args:
            mode: Filter mode string to validate.
        
        Returns:
            True if the mode is valid or None, False otherwise.
        """
        if mode is None or mode == "":
            return True
        
        normalized = mode.casefold() if isinstance(mode, str) else None
        return normalized in self.VALID_MODES
