"""Desktop coordinator composition root.

The implementation lives in ``main_coordinator`` during the responsibility
migration, but the public startup boundary intentionally exposes only the new
runtime name.  No ``MainCoordinator`` compatibility alias is provided.
"""

from .main_coordinator import DesktopCoordinatorRuntime

__all__ = ["DesktopCoordinatorRuntime"]
