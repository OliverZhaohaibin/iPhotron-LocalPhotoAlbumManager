"""Structural contract for GUI runtime entry objects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from ...di.container import DependencyContainer
    from ...events.bus import EventBus
    from ...gui.facade import AppFacade
    from ...gui.ui.theme_manager import ThemeManager
    from ...infrastructure.services.library_asset_runtime import LibraryAssetRuntime
    from ...library.runtime_controller import LibraryRuntimeController
    from ...settings.manager import SettingsManager
    from ...bootstrap.library_session import LibrarySession


@runtime_checkable
class RuntimeEntryContract(Protocol):
    """Small runtime surface shared by RuntimeContext and AppContext."""

    settings: "SettingsManager"
    library: "LibraryRuntimeController"
    facade: "AppFacade"
    theme: "ThemeManager"
    event_bus: "EventBus"
    container: "DependencyContainer"
    asset_runtime: "LibraryAssetRuntime"
    library_session: "LibrarySession | None"
    recent_albums: list[Path]
    defer_startup_tasks: bool

    def open_library(self, root: Path) -> "LibrarySession":
        """Bind and return the active library session."""

    def close_library(self) -> None:
        """Close the active library session."""

    def resume_startup_tasks(self) -> None:
        """Run deferred startup work."""

    def note_user_interaction(self) -> None:
        """Record interactive activity that may defer background startup work."""

    def resume_pending_startup_scan_now(self) -> bool:
        """Start any queued startup scan immediately."""

    def remember_album(self, root: Path) -> None:
        """Track *root* in the recent albums list."""
