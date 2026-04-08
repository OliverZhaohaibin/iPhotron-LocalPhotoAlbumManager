"""Formal API contract for the runtime entry point.

This module defines :class:`RuntimeEntryContract` – the abstract interface
that every runtime entry object must satisfy.  Code written in Phase 4 and
beyond must depend on this protocol, not on any concrete class.

Motivation
----------
Declaring the contract explicitly:

* prevents ``RuntimeContext`` from drifting into a god-object,
* makes the dependency boundary visible to static type-checkers,
* lets tests use lightweight fakes that satisfy the protocol without
  instantiating the full application stack.

Usage
-----
New code should type-annotate its dependencies as ``RuntimeEntryContract``::

    from iPhoto.application.contracts import RuntimeEntryContract

    def my_component(ctx: RuntimeEntryContract) -> None:
        library = ctx.library
        settings = ctx.settings
        ...

Legacy code that still holds an ``AppContext`` may continue to work for
attribute access because ``AppContext`` forwards properties to an underlying
``RuntimeContext`` instance.  However, the structural protocol is satisfied by
``RuntimeContext`` itself, not by ``AppContext``.  Callers that need a
``RuntimeEntryContract`` should pass the wrapped runtime object (for example,
``ctx._runtime``) rather than the ``AppContext`` wrapper.

Rules
-----
* Do **NOT** add business logic to implementations of this contract.
* Do **NOT** import ``AppContext`` from new code – use ``RuntimeContext`` or
  a test-double that satisfies ``RuntimeEntryContract``.
* Callers must not rely on implementation-specific attributes not declared
  here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

# TYPE_CHECKING imports are used solely to avoid circular imports at runtime
# while still allowing type-checkers to resolve the referenced types.
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ...di.container import DependencyContainer
    from ...gui.facade import AppFacade
    from ...library.manager import LibraryManager
    from ...settings.manager import SettingsManager


@runtime_checkable
class RuntimeEntryContract(Protocol):
    """Structural protocol for the authoritative runtime entry point.

    Any object that exposes these attributes and methods is considered a valid
    runtime entry point.  Both :class:`~iPhoto.bootstrap.runtime_context.RuntimeContext`
    and lightweight test doubles satisfy this protocol automatically (structural
    typing – no explicit ``implements`` declaration required).

    Attributes
    ----------
    settings:
        Application-wide settings manager (read/write).
    library:
        The shared library manager (thin composition shell).
    facade:
        The Qt application facade used for signal routing.
    container:
        The assembled DI container.
    recent_albums:
        Most-recently-opened album paths.
    """

    @property
    def settings(self) -> "SettingsManager":
        """Application settings manager."""
        ...

    @property
    def library(self) -> "LibraryManager":
        """Shared library manager."""
        ...

    @property
    def facade(self) -> "AppFacade":
        """Qt application facade."""
        ...

    @property
    def container(self) -> "DependencyContainer":
        """DI container."""
        ...

    @property
    def recent_albums(self) -> list[Path]:
        """Recently opened album paths."""
        ...

    def resume_startup(self) -> None:
        """Run deferred startup tasks.

        Must be idempotent – calling it multiple times must not raise.
        """
        ...

    def remember_album(self, root: Path) -> None:
        """Record *root* in the recent-albums list.

        Implementations must update :attr:`recent_albums` after recording.
        """
        ...


__all__ = ["RuntimeEntryContract"]
