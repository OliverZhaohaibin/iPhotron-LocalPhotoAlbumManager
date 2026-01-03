"""Asset list model package with modular architecture.

This package provides a clean separation of concerns for the AssetListModel:

- `streaming`: Buffering and throttling logic for responsive chunk loading
- `transactions`: Optimistic UI updates for move/delete operations
- `filter_engine`: In-memory filtering by asset type and status
- `orchestrator`: Data loading orchestration and worker management
- `refresh_handler`: Incremental update handling via diff & patch
- `resolver`: Path and metadata resolution

The main AssetListModel class can be found in the parent directory's
asset_list_model.py file, which coordinates these components.
"""

from .streaming import AssetStreamBuffer
from .transactions import OptimisticTransactionManager
from .filter_engine import ModelFilterHandler
from .orchestrator import AssetDataOrchestrator
from .refresh_handler import IncrementalUpdateHandler
from .resolver import AssetPathResolver
from .model import AssetListModel

__all__ = [
    "AssetStreamBuffer",
    "OptimisticTransactionManager",
    "ModelFilterHandler",
    "AssetDataOrchestrator",
    "IncrementalUpdateHandler",
    "AssetPathResolver",
    "AssetListModel",
]
