"""Compatibility shell for legacy GUI callers.

New runtime-aware code should use :class:`iPhoto.bootstrap.runtime_context.RuntimeContext`
directly.  This module intentionally preserves the old ``AppContext`` name so
older GUI code can migrate incrementally without re-owning dependency wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .bootstrap.runtime_context import RuntimeContext
    from .di.container import DependencyContainer
    from .events.bus import EventBus
    from .gui.facade import AppFacade
    from .gui.ui.theme_manager import ThemeManager
    from .bootstrap.library_session import LibrarySession
    from .library.manager import LibraryManager
    from .settings.manager import SettingsManager


class AppContext:
    """Backwards-compatible proxy around :class:`RuntimeContext`."""

    def __init__(self, defer_startup_tasks: bool = False) -> None:
        from .bootstrap.runtime_context import RuntimeContext

        self._runtime: RuntimeContext = RuntimeContext.create(
            defer_startup=defer_startup_tasks
        )
        self.defer_startup_tasks = defer_startup_tasks

    @property
    def settings(self) -> "SettingsManager":
        return self._runtime.settings

    @property
    def library(self) -> "LibraryManager":
        return self._runtime.library

    @property
    def facade(self) -> "AppFacade":
        return self._runtime.facade

    @property
    def event_bus(self) -> "EventBus":
        return self._runtime.event_bus

    @property
    def container(self) -> "DependencyContainer":
        return self._runtime.container

    @property
    def theme(self) -> "ThemeManager":
        return self._runtime.theme

    @property
    def asset_runtime(self):
        return self._runtime.asset_runtime

    @property
    def library_session(self) -> "LibrarySession | None":
        return self._runtime.library_session

    @property
    def recent_albums(self) -> list[Path]:
        return self._runtime.recent_albums

    def resume_startup_tasks(self) -> None:
        self._runtime.resume_startup_tasks()

    def remember_album(self, root: Path) -> None:
        self._runtime.remember_album(root)

    def open_library(self, root: Path) -> "LibrarySession":
        return self._runtime.open_library(root)

    def close_library(self) -> None:
        self._runtime.close_library()
