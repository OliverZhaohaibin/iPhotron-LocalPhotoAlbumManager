"""GUI package for the iPhoto application.

Keep package import deliberately light: importing ``iPhoto.gui.main`` is the
first Python step of desktop startup and must not pull the complete service
layer in through :class:`AppFacade`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing-only compatibility export
    from .facade import AppFacade

__all__ = ["AppFacade"]


def __getattr__(name: str) -> Any:
    """Resolve compatibility exports without penalising GUI startup."""

    if name == "AppFacade":
        from .facade import AppFacade

        return AppFacade
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
