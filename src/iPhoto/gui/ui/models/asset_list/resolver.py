"""Path and metadata resolution for asset list model.

This module handles translation between absolute paths (from OS/UI) and
relative paths (internal storage), with fallback to recently removed cache.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .....utils.pathutils import normalise_rel_value

logger = logging.getLogger(__name__)


class AssetPathResolver:
    """Resolves paths to metadata and handles recently removed cache.
    
    This class provides:
    - Absolute path to relative path conversion
    - Metadata lookup by path
    - Fallback to recently removed cache
    - Path normalization and error handling
    """
    
    def __init__(
        self,
        get_rows: Callable[[], list],
        get_row_lookup: Callable[[], Dict[str, int]],
        get_abs_lookup: Callable[[str], Optional[int]],
        get_recently_removed: Callable[[str], Optional[Dict[str, Any]]],
        album_root_getter: Callable[[], Optional[Path]],
    ):
        """Initialize the path resolver.
        
        Args:
            get_rows: Callback to get current model rows.
            get_row_lookup: Callback to get rel->index lookup dict.
            get_abs_lookup: Callback to lookup index by absolute path.
            get_recently_removed: Callback to get recently removed metadata.
            album_root_getter: Callback to get current album root.
        """
        self._get_rows = get_rows
        self._get_row_lookup = get_row_lookup
        self._get_abs_lookup = get_abs_lookup
        self._get_recently_removed = get_recently_removed
        self._get_album_root = album_root_getter
    
    def metadata_for_absolute_path(self, path: Path) -> Optional[Dict[str, object]]:
        """Return the cached metadata row for *path* if it belongs to the model.
        
        The asset grid frequently passes absolute filesystem paths around when
        triggering operations such as copy or delete. Internally the model
        indexes rows by their path relative to the album root, so this helper
        normalises the provided *path* to the same representation and resolves
        the matching row when possible.
        
        When the file no longer sits inside the current root—because it was
        moved externally or is part of a transient virtual collection—the
        method gracefully falls back to a direct absolute comparison so callers
        still receive metadata whenever it is available.
        
        Args:
            path: Absolute filesystem path to resolve.
        
        Returns:
            Metadata dictionary if found, None otherwise.
        """
        rows = self._get_rows()
        if not rows:
            return None
        
        album_root = self._get_album_root()
        try:
            normalized_path = path.resolve()
        except OSError:
            normalized_path = path
        
        # Try relative path lookup first
        if album_root is not None:
            try:
                normalized_root = album_root.resolve()
            except OSError:
                normalized_root = album_root
            
            try:
                rel_key = normalized_path.relative_to(normalized_root).as_posix()
            except ValueError:
                rel_key = None
            else:
                row_index = self._get_row_lookup().get(rel_key)
                if row_index is not None and 0 <= row_index < len(rows):
                    return rows[row_index]
        
        # Try absolute path lookup (O(1) optimization)
        normalized_str = str(normalized_path)
        row_index = self._get_abs_lookup(normalized_str)
        if row_index is not None and 0 <= row_index < len(rows):
            return rows[row_index]
        
        # Fall back to recently removed cache
        # This allows operations triggered right after an optimistic removal
        # to still access metadata
        cached = self._get_recently_removed(normalized_str)
        if cached is not None:
            return cached
        
        return None
    
    def resolve_rel_to_abs(self, rel: str) -> Optional[Path]:
        """Resolve a relative path to absolute path.
        
        Args:
            rel: Relative path string.
        
        Returns:
            Absolute Path if album root is set, None otherwise.
        """
        album_root = self._get_album_root()
        if not album_root:
            return None
        
        try:
            return (album_root / rel).resolve()
        except (OSError, ValueError) as e:
            logger.warning("Failed to resolve relative path %s: %s", rel, e)
            return None
    
    def resolve_abs_to_rel(self, abs_path: Path) -> Optional[str]:
        """Resolve an absolute path to relative path.
        
        Args:
            abs_path: Absolute filesystem path.
        
        Returns:
            Relative path string if within album root, None otherwise.
        """
        album_root = self._get_album_root()
        if not album_root:
            return None
        
        try:
            normalized_path = abs_path.resolve()
            normalized_root = album_root.resolve()
            return normalized_path.relative_to(normalized_root).as_posix()
        except (OSError, ValueError) as e:
            logger.debug("Path %s not relative to album root: %s", abs_path, e)
            return None
    
    def normalize_rel(self, rel: str) -> str:
        """Normalize a relative path for consistent lookups.
        
        Args:
            rel: Relative path string.
        
        Returns:
            Normalized relative path.
        """
        return normalise_rel_value(rel)
