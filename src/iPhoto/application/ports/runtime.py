"""Runtime adapter ports."""

from __future__ import annotations

from typing import Any, Protocol


class MapRuntimePort(Protocol):
    """Optional maps runtime boundary."""

    def is_available(self) -> bool:
        """Return whether map rendering/search is available."""


class TaskSchedulerPort(Protocol):
    """Background task lifecycle boundary."""

    def submit(self, task: Any) -> Any:
        """Submit a task and return an implementation-defined handle."""

    def cancel(self, handle: Any) -> None:
        """Cancel a submitted task when possible."""
