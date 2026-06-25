"""Authoritative local persistence for user-assigned asset locations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iPhoto.application.ports import (
    LocationAssignmentRepositoryPort,
    LocationWriteJobRecord,
)
from iPhoto.events.asset_events import AssetMetadataUpdated
from iPhoto.events.bus import EventBus


@dataclass(frozen=True)
class LocationAssignment:
    asset_path: Path
    asset_rel: str
    display_name: str
    gps: dict[str, float]
    metadata: dict[str, Any]
    write_job: LocationWriteJobRecord


class LocationAssignmentService:
    """Persist local geodata first, then queue best-effort original write-back."""

    def __init__(
        self,
        assignment_repository: LocationAssignmentRepositoryPort,
        event_bus: EventBus | None = None,
    ) -> None:
        self._assignment_repository = assignment_repository
        self._event_bus = event_bus
        self._revision = 0

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
    ) -> LocationAssignment:
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("Location display name must not be empty")
        gps = {"lat": float(latitude), "lon": float(longitude)}
        refreshed_metadata = dict(existing_metadata or {})
        refreshed_metadata["gps"] = dict(gps)
        refreshed_metadata["location"] = normalized_name
        refreshed_metadata["location_name"] = normalized_name
        refreshed_metadata.setdefault("place", normalized_name)

        job = self._assignment_repository.assign_location(
            asset_rel=asset_rel,
            asset_path=asset_path,
            gps=gps,
            location=normalized_name,
            is_video=is_video,
            metadata_updates=refreshed_metadata,
        )
        self._revision += 1
        if self._event_bus is not None:
            self._event_bus.publish(
                AssetMetadataUpdated(
                    asset_path=Path(asset_path),
                    asset_rel=str(asset_rel),
                    gps=dict(gps),
                    location=normalized_name,
                    metadata_delta=dict(refreshed_metadata),
                    revision=self._revision,
                    source="location_assignment",
                )
            )

        return LocationAssignment(
            asset_path=Path(asset_path),
            asset_rel=str(asset_rel),
            display_name=normalized_name,
            gps=gps,
            metadata=refreshed_metadata,
            write_job=job,
        )


__all__ = ["LocationAssignment", "LocationAssignmentService"]
