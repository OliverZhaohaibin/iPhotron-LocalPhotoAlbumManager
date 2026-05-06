"""Media, metadata, thumbnail, and edit sidecar ports."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class MediaScannerPort(Protocol):
    """Discover media rows for a scan scope without deciding persistence policy."""

    def scan(
        self,
        root: Path,
        include: Iterable[str],
        exclude: Iterable[str],
        *,
        existing_rows_resolver: Callable[[list[str]], dict[str, dict[str, Any]]] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        generate_micro_thumbnails: bool | Callable[[], bool] = True,
    ) -> Iterator[dict[str, Any]]:
        """Yield normalized scan rows."""


class MetadataReaderPort(Protocol):
    """Read and normalize media metadata."""

    def get_metadata_batch(self, paths: list[Path]) -> list[dict[str, Any]]:
        """Return raw metadata payloads for *paths*."""

    def normalize_metadata(
        self,
        root: Path,
        file_path: Path,
        raw_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize one metadata payload into the index row shape."""


class MetadataWriterPort(Protocol):
    """Best-effort writer for explicit user metadata updates."""

    def write_gps_metadata(
        self,
        path: Path,
        *,
        latitude: float,
        longitude: float,
        is_video: bool,
    ) -> None:
        """Write GPS metadata to an original media file."""


class LocationMetadataPort(Protocol):
    """Metadata boundary for explicit user location assignment."""

    def write_gps_metadata(
        self,
        path: Path,
        *,
        latitude: float,
        longitude: float,
        is_video: bool,
    ) -> None:
        """Best-effort write GPS metadata to an original media file."""

    def read_back_metadata(
        self,
        path: Path,
        *,
        is_video: bool,
        existing_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return refreshed metadata after a successful file write."""


class ThumbnailRendererPort(Protocol):
    """Render thumbnails without exposing GUI or worker ownership."""

    def render_thumbnail(self, path: Path, size: tuple[int, int]) -> Any:
        """Return a thumbnail payload for *path* at *size*."""


class EditSidecarPort(Protocol):
    """Read and write non-destructive edit sidecars."""

    def sidecar_exists(self, path: Path) -> bool:
        """Return ``True`` when *path* has a persisted sidecar file."""

    def read_adjustments(self, path: Path) -> dict[str, Any]:
        """Read persisted edit adjustments."""

    def write_adjustments(self, path: Path, adjustments: dict[str, Any]) -> None:
        """Persist edit adjustments atomically."""


@dataclass(frozen=True)
class EditRenderingState:
    """Resolved edit metadata for one asset."""

    sidecar_exists: bool
    raw_adjustments: dict[str, Any]
    resolved_adjustments: dict[str, Any]
    adjusted_preview: bool
    has_visible_edits: bool
    trim_range_ms: tuple[int, int] | None
    effective_duration_sec: float | None


class EditServicePort(Protocol):
    """Library-scoped edit sidecar and render-state surface."""

    def sidecar_exists(self, path: Path) -> bool:
        """Return ``True`` when *path* has a persisted sidecar file."""

    def read_adjustments(self, path: Path) -> dict[str, Any]:
        """Read persisted edit adjustments."""

    def write_adjustments(self, path: Path, adjustments: dict[str, Any]) -> None:
        """Persist edit adjustments atomically."""

    def default_adjustments(self) -> dict[str, Any]:
        """Return the canonical default edit-session values."""

    def describe_adjustments(
        self,
        path: Path,
        *,
        duration_hint: float | None = None,
        color_stats: Any | None = None,
    ) -> EditRenderingState:
        """Return resolved edit metadata for *path*."""
