"""Runtime adapter ports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from typing import Literal

from ..dtos import GeotaggedAsset, MapMarkerActivation


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


class MapInteractionServicePort(Protocol):
    """Library-scoped map marker interaction boundary."""

    def activate_marker_assets(
        self,
        assets: object,
    ) -> MapMarkerActivation:
        """Return the routing decision for a clicked marker asset payload."""


class LocationAssetServicePort(Protocol):
    """Library-scoped location asset query boundary."""

    def list_geotagged_assets(self) -> list[GeotaggedAsset]:
        """Return every visible asset with GPS metadata."""

    def asset_from_row(self, row: object) -> GeotaggedAsset | None:
        """Convert one scan/index row to a geotagged asset when possible."""

    def invalidate_cache(self) -> None:
        """Drop cached location assets after scan or state changes."""


class AssetStateServicePort(Protocol):
    """Library-scoped durable asset-state command boundary."""

    def toggle_favorite(self, path: Path) -> bool:
        """Toggle favorite state for *path* and return the new state."""


class TaskSchedulerPort(Protocol):
    """Background task lifecycle boundary."""

    def submit(self, task: Any) -> Any:
        """Submit a task and return an implementation-defined handle."""

    def cancel(self, handle: Any) -> None:
        """Cancel a submitted task when possible."""
