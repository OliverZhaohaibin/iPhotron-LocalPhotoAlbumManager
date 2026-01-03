"""Asset index storage package.

This package provides a modular architecture for asset persistence:

- `repository`: High-level API for CRUD operations (main entry point)
- `engine`: Low-level database connection management
- `migrations`: Schema initialization and updates
- `recovery`: Database corruption recovery
- `queries`: SQL query construction utilities

For backward compatibility, the main `IndexStore` class is re-exported
from the repository module.
"""
from .repository import AssetRepository as IndexStore
from .repository import GLOBAL_INDEX_DB_NAME

__all__ = ["IndexStore", "GLOBAL_INDEX_DB_NAME"]
