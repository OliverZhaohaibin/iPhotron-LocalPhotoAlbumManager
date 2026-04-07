"""Application-wide context helpers for the GUI layer.

Compatibility shell.

This module retains the ``AppContext`` name and API surface for backward
compatibility.  All dependency wiring is now delegated to
``bootstrap/runtime_context.py`` (Phase 3).  ``AppContext`` acts as a thin
proxy that forwards every public attribute to the underlying
:class:`~iPhoto.bootstrap.runtime_context.RuntimeContext` instance.

Do NOT add business logic or new dependency construction to this file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover
    from .bootstrap.runtime_context import RuntimeContext
    from .di.container import DependencyContainer
    from .gui.facade import AppFacade
    from .gui.ui.theme_manager import ThemeManager
    from .library.manager import LibraryManager
    from .presentation.qt.session.app_session import AppSession
    from .settings.manager import SettingsManager

_logger = logging.getLogger(__name__)


class AppContext:
    """Compatibility shell: delegates all wiring to :class:`RuntimeContext`.

    New code must use :class:`~iPhoto.bootstrap.runtime_context.RuntimeContext`
    directly.  This class exists solely to preserve the ``AppContext`` API
    surface for callers written before Phase 3.
    """

    def __init__(self, defer_startup_tasks: bool = False) -> None:
        from .bootstrap.runtime_context import RuntimeContext
        from .gui.ui.theme_manager import ThemeManager

        self.defer_startup_tasks = defer_startup_tasks
        self._pending_basic_library_path: Path | None = None

        # RuntimeContext is the single authoritative dependency-wiring point.
        self._runtime: RuntimeContext = RuntimeContext.create(defer_startup=defer_startup_tasks)

        # ThemeManager is GUI-layer state and lives here rather than in RuntimeContext.
        self.theme: ThemeManager = ThemeManager(self._runtime.settings)
        self.theme.apply_theme()

        # Mirror recent_albums so existing callers can read / mutate the list.
        self.recent_albums: List[Path] = list(self._runtime.recent_albums)

    # ------------------------------------------------------------------
    # Dependency proxies – forward to RuntimeContext
    # ------------------------------------------------------------------

    @property
    def settings(self) -> "SettingsManager":
        """Application settings manager."""
        return self._runtime.settings

    @property
    def library(self) -> "LibraryManager":
        """Shared library manager."""
        return self._runtime.library

    @property
    def facade(self) -> "AppFacade":
        """Qt application facade."""
        return self._runtime.facade

    @property
    def container(self) -> "DependencyContainer":
        """DI container assembled by :func:`~iPhoto.bootstrap.container.build_container`."""
        return self._runtime.container

    @property
    def session(self) -> "AppSession":
        """Active application session."""
        return self._runtime._session

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def resume_startup_tasks(self) -> None:
        """Run deferred startup work such as binding the default library path."""

        self._runtime.resume_startup()
        self.recent_albums = list(self._runtime.recent_albums)

    def remember_album(self, root: Path) -> None:
        """Track *root* in the recent albums list, keeping the most recent first."""

        self._runtime.remember_album(root)
        self.recent_albums = list(self._runtime.recent_albums)
