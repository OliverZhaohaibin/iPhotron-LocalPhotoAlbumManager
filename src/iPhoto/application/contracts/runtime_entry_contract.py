"""Structural contract for GUI runtime entry objects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from ...di.container import DependencyContainer
    from ...gui.facade import AppFacade
    from ...gui.ui.theme_manager import ThemeManager
    from ...infrastructure.services.library_asset_runtime import LibraryAssetRuntime
    from ...library.manager import LibraryManager
    from ...settings.manager import SettingsManager


@runtime_checkable
class RuntimeEntryContract(Protocol):
    """Small runtime surface shared by RuntimeContext and AppContext."""

    settings: "SettingsManager"
    library: "LibraryManager"
    facade: "AppFacade"
    theme: "ThemeManager"
    container: "DependencyContainer"
    asset_runtime: "LibraryAssetRuntime"
    recent_albums: list[Path]
    defer_startup_tasks: bool

    def resume_startup_tasks(self) -> None:
        """Run deferred startup work."""

    def remember_album(self, root: Path) -> None:
        """Track *root* in the recent albums list."""
