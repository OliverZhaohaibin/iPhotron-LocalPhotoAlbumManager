"""
Persistent storage for album index rows.

This module provides read/write access to the global asset index. The index is
stored in an SQLite database (`global_index.db`) located at the library root
under `.iphoto/`. This centralized architecture replaces the previous per-album
`index.db` design, enabling:

1. **Flat Storage with Path Indexing**: A single `assets` table stores all media
   files with their library-relative paths (`rel` column) and parent album
   paths (`parent_album_path`) for hierarchical queries.

2. **K-Way Merge via SQLite**: Composite indexes on `(parent_album_path, dt, id)`
   allow the database engine to perform efficient sorted retrieval across albums.

3. **Cursor-Based Pagination**: Seek pagination using `(dt, id) < (?, ?)` avoids
   the O(N) cost of OFFSET-based pagination for large datasets.

The `IndexStore` class manages the creation, reading, updating, and deletion
of asset records in the SQLite database.

.. note::
   This module has been refactored into a package structure for better
   maintainability. The original `IndexStore` class is now available as
   `AssetRepository` in the `index_store.repository` module. This file
   maintains backward compatibility by re-exporting the class.
"""
from __future__ import annotations

# Re-export the main class and utilities for backward compatibility
from .index_store.repository import IndexStore, GLOBAL_INDEX_DB_NAME
from .index_store.queries import normalize_path, escape_like_pattern

__all__ = ["IndexStore", "GLOBAL_INDEX_DB_NAME", "normalize_path", "escape_like_pattern"]

# For backward compatibility, alias the old class name
# Users can continue to use: from iPhoto.cache.index_store import IndexStore
