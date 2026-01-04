"""Asset index storage package.

This package provides a modular architecture for asset persistence using a
**Single Global Database** pattern:

- `repository`: High-level API for CRUD operations (main entry point)
- `engine`: Low-level database connection management
- `migrations`: Schema initialization and updates
- `recovery`: Database corruption recovery
- `queries`: SQL query construction utilities
- `kway_merge`: K-Way merge for efficient multi-source aggregation

Architecture:
    The package enforces a unified write pipeline where all database operations
    go through the `AssetRepository` (aliased as `IndexStore`). Key principles:
    
    - **Single Global Database**: One database at `<library_root>/.iPhoto/global_index.db`
    - **Single Write Gateway**: All writes through `AssetRepository`
    - **Idempotent Writes**: Duplicate scans don't create duplicates (INSERT OR REPLACE)
    - **Additive-Only Scans**: Scanning never deletes data from the database

Usage:
    For long-running applications (GUI, services) that need connection pooling:
        from iPhoto.cache.index_store import get_global_repository
        store = get_global_repository(library_root)
    
    For CLI tools and short-lived operations:
        from iPhoto.cache.index_store import IndexStore
        store = IndexStore(library_root)
    
    Both approaches use the same database file, ensuring data consistency.
    
    For cursor-based pagination (recommended for large datasets):
        from iPhoto.cache.index_store import IndexStore, CursorPage, PaginationCursor
        store = IndexStore(library_root)
        page = store.fetch_by_cursor(limit=50)
        while page.has_more:
            process(page.items)
            page = store.fetch_by_cursor(cursor=page.next_cursor, limit=50)
    
    For merging multiple album sources (e.g., "All Photos" view):
        from iPhoto.cache.index_store import (
            IndexStore, KWayMergeProvider, AssetIterator, create_all_photos_provider
        )
        store = IndexStore(library_root)
        provider = create_all_photos_provider(store)
        for asset in provider:
            display(asset)
"""
from .kway_merge import AssetIterator, KWayMergeProvider, create_all_photos_provider
from .repository import (
    GLOBAL_INDEX_DB_NAME,
    CursorPage,
    PaginationCursor,
    get_global_repository,
    reset_global_repository,
)
from .repository import AssetRepository as IndexStore

__all__ = [
    "AssetIterator",
    "CursorPage",
    "GLOBAL_INDEX_DB_NAME",
    "IndexStore",
    "KWayMergeProvider",
    "PaginationCursor",
    "create_all_photos_provider",
    "get_global_repository",
    "reset_global_repository",
]
