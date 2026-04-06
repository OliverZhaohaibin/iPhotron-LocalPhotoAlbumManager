"""Application-wide context helpers for the GUI layer.

Compatibility shell.

This module retains the ``AppContext`` name and API surface for backward
compatibility.  New dependency wiring must go into
``bootstrap/container.py`` and new GUI session state must go into
``presentation/qt/session/app_session.py``.  This file composes both and
must not grow further.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, TYPE_CHECKING
import logging
from typing import Optional

from .di.container import DependencyContainer
from .bootstrap.container import build_container

if TYPE_CHECKING:  # pragma: no cover - only for type checking
    from .gui.facade import AppFacade
    from .library.manager import LibraryManager
    from .settings.manager import SettingsManager

_logger = logging.getLogger(__name__)


def _create_facade() -> "AppFacade":
    """Factory that imports :class:`AppFacade` lazily to avoid circular imports."""

    from .gui.facade import AppFacade  # Local import prevents circular dependency

    return AppFacade()


def _create_settings_manager():
    from .settings.manager import SettingsManager

    manager = SettingsManager()
    manager.load()
    return manager


def _create_library_manager():
    from .library.manager import LibraryManager

    return LibraryManager()


@dataclass
class AppContext:
    """Compatibility shell: composes the DI container and GUI session state.

    New code must access dependencies via ``context.container`` and session
    state via ``context.session``.  Direct attributes on this class are
    preserved for backward compatibility only.
    """

    settings: "SettingsManager" = field(default_factory=_create_settings_manager)
    library: "LibraryManager" = field(default_factory=_create_library_manager)
    facade: "AppFacade" = field(default_factory=_create_facade)
    recent_albums: List[Path] = field(default_factory=list)
    theme: "ThemeManager" = field(init=False)

    # DI Container – assembled by bootstrap/container.py
    container: DependencyContainer = field(default_factory=build_container)
    defer_startup_tasks: bool = False
    _pending_basic_library_path: Path | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        from .errors import LibraryError
        from .gui.ui.theme_manager import ThemeManager
        from .presentation.qt.session.app_session import AppSession

        self.theme = ThemeManager(self.settings)
        self.theme.apply_theme()

        # ``AppFacade`` needs to observe the shared library manager so that
        # manifest writes performed while browsing nested albums can keep the
        # global "Favorites" collection in sync.
        self.facade.bind_library(self.library)

        # Build the canonical session object.  Startup tasks are deferred to
        # the session so new code can interact with ``context.session`` directly.
        self.session = AppSession(
            settings=self.settings,
            library=self.library,
            facade=self.facade,
            defer_startup_tasks=self.defer_startup_tasks,
        )
        # Sync recent_albums back from session after it loads persisted history.
        self.recent_albums = self.session.recent_albums

    def resume_startup_tasks(self) -> None:
        """Run deferred startup work such as binding the default library path."""

        self.session.resume_startup_tasks()
        self.recent_albums = self.session.recent_albums

    def remember_album(self, root: Path) -> None:
        """Track *root* in the recent albums list, keeping the most recent first."""

        self.session.remember_album(root)
        self.recent_albums = self.session.recent_albums
