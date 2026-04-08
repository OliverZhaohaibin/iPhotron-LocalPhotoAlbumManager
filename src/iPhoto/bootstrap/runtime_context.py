"""Runtime context – formal application entry point for new code.

This module is the **authoritative** runtime context for code written in and
after Phase 3.  It replaces ``AppContext`` as the dependency source for all
*new* code.

Contract
--------
``RuntimeContext`` satisfies :class:`~iPhoto.application.contracts.RuntimeEntryContract`
(a structural Protocol).  Code that depends on the runtime entry point should
type-annotate its parameter as ``RuntimeEntryContract`` rather than the
concrete ``RuntimeContext`` class, so that lightweight test doubles can be
substituted freely.

Migration guide
---------------
Old code (Phase 1/2 compatibility path)::

    from iPhoto.appctx import AppContext
    ctx = AppContext()
    ctx.library.bind_path(...)

New code (Phase 3+)::

    from iPhoto.bootstrap.runtime_context import RuntimeContext
    ctx = RuntimeContext.create()
    ctx.library.bind_path(...)

Relationship to ``AppContext``
------------------------------
``AppContext`` remains as a compatibility shell for existing callers.  It
composes ``RuntimeContext`` internally so both share the same underlying
objects.  New code must **not** depend on ``AppContext`` directly.

Do NOT add business logic to this module.  Its sole responsibility is wiring
and composition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..di.container import DependencyContainer
from .container import build_container

if TYPE_CHECKING:  # pragma: no cover
    from ..gui.facade import AppFacade
    from ..library.manager import LibraryManager
    from ..settings.manager import SettingsManager

_logger = logging.getLogger(__name__)


def _make_library_manager() -> LibraryManager:
    from ..library.manager import LibraryManager

    return LibraryManager()


def _make_settings_manager() -> SettingsManager:
    from ..settings.manager import SettingsManager

    manager = SettingsManager()
    manager.load()
    return manager


def _make_facade() -> AppFacade:
    from ..gui.facade import AppFacade

    return AppFacade()


@dataclass
class RuntimeContext:
    """Formal runtime context for Phase 3+ code.

    Attributes
    ----------
    settings:
        Application settings manager.
    library:
        Library manager (thin composition shell after Phase 3).
    facade:
        Application facade for Qt signal routing.
    container:
        DI container assembled by :func:`~iPhoto.bootstrap.container.build_container`.
    recent_albums:
        Most-recently-opened album paths (loaded from settings on startup).
    """

    settings: SettingsManager = field(default_factory=_make_settings_manager)
    library: LibraryManager = field(default_factory=_make_library_manager)
    facade: AppFacade = field(default_factory=_make_facade)
    container: DependencyContainer = field(default_factory=build_container)
    recent_albums: list[Path] = field(default_factory=list)

    # Internal flag – allows tests to skip expensive startup work.
    _defer_startup: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.facade.bind_library(self.library)

        from ..presentation.qt.session.app_session import AppSession

        self._session = AppSession(
            settings=self.settings,
            library=self.library,
            facade=self.facade,
            defer_startup_tasks=self._defer_startup,
        )
        self.recent_albums = self._session.recent_albums

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, *, defer_startup: bool = False) -> RuntimeContext:
        """Create a fully initialised ``RuntimeContext``.

        Parameters
        ----------
        defer_startup:
            When ``True``, expensive startup tasks (library binding, initial
            scan) are deferred until :meth:`resume_startup` is called.  Useful
            for testing and for splitting application initialisation across
            multiple Qt event-loop iterations.
        """

        return cls(_defer_startup=defer_startup)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def resume_startup(self) -> None:
        """Run deferred startup tasks (library binding, initial scan)."""

        self._session.resume_startup_tasks()
        self.recent_albums = self._session.recent_albums

    def remember_album(self, root: Path) -> None:
        """Record *root* in the recent-albums list."""

        self._session.remember_album(root)
        self.recent_albums = self._session.recent_albums


__all__ = ["RuntimeContext"]
