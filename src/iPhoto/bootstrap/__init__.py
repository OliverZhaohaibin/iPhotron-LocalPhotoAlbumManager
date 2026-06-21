"""Runtime/bootstrap entry points for the GUI application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .runtime_context import RuntimeContext

__all__ = ["RuntimeContext"]


def __getattr__(name: str) -> Any:
    if name == "RuntimeContext":
        from .runtime_context import RuntimeContext

        return RuntimeContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
