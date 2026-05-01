"""Runtime adapter ports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from typing import Literal


MapBackendKind = Literal[
    "osmand_native",
    "osmand_python",
    "legacy_python",
    "unavailable",
]


@dataclass(frozen=True)
class MapRuntimeCapabilities:
    """Describe the currently selected map runtime behaviour."""

    display_available: bool
    preferred_backend: MapBackendKind
    python_gl_available: bool
    native_widget_available: bool
    osmand_extension_available: bool
    location_search_available: bool
    status_message: str


class MapRuntimePort(Protocol):
    """Optional maps runtime boundary."""

    def is_available(self) -> bool:
        """Return whether map rendering/search is available."""

    def capabilities(self) -> MapRuntimeCapabilities:
        """Return the current runtime capability snapshot."""

    def package_root(self) -> Path | None:
        """Return the maps package root bound to the current runtime."""


class TaskSchedulerPort(Protocol):
    """Background task lifecycle boundary."""

    def submit(self, task: Any) -> Any:
        """Submit a task and return an implementation-defined handle."""

    def cancel(self, handle: Any) -> None:
        """Cancel a submitted task when possible."""
