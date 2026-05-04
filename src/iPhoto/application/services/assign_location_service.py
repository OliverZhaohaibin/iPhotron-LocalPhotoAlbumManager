"""Application service for embedding and persisting user-assigned locations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iPhoto.application.ports import LibraryStateRepositoryPort, LocationMetadataPort
from iPhoto.errors import ExternalToolError


@dataclass(frozen=True)
class AssignedLocationResult:
    asset_path: Path
    asset_rel: str
    display_name: str
    gps: dict[str, float]
    metadata: dict[str, Any]
    file_write_error: str | None = None


class AssignLocationService:
    """Persist assigned locations and best-effort embed GPS metadata in files."""

    def __init__(
        self,
        state_repository: LibraryStateRepositoryPort,
        metadata: LocationMetadataPort,
    ) -> None:
        self._state_repository = state_repository
        self._metadata = metadata

    def assign(
        self,
        *,
        asset_path: Path,
        asset_rel: str,
        display_name: str,
        latitude: float,
        longitude: float,
        is_video: bool,
        existing_metadata: dict[str, Any] | None = None,
    ) -> AssignedLocationResult:
        normalized_name = display_name.strip()
        gps = {"lat": float(latitude), "lon": float(longitude)}
        file_write_error: str | None = None

        try:
            self._metadata.write_gps_metadata(
                asset_path,
                latitude=gps["lat"],
                longitude=gps["lon"],
                is_video=is_video,
            )
        except (ExternalToolError, OSError) as exc:
            file_write_error = str(exc)
            refreshed_metadata = dict(existing_metadata or {})
        else:
            refreshed_metadata = self._metadata.read_back_metadata(
                asset_path,
                is_video=is_video,
                existing_metadata=existing_metadata,
            )
        refreshed_metadata["gps"] = dict(gps)
        refreshed_metadata["location"] = normalized_name
        refreshed_metadata["location_name"] = normalized_name

        self._state_repository.update_asset_geodata(
            asset_rel,
            gps=gps,
            location=normalized_name,
            metadata_updates=refreshed_metadata,
        )
        return AssignedLocationResult(
            asset_path=Path(asset_path),
            asset_rel=str(asset_rel),
            display_name=normalized_name,
            gps=gps,
            metadata=refreshed_metadata,
            file_write_error=file_write_error,
        )


__all__ = ["AssignLocationService", "AssignedLocationResult"]
