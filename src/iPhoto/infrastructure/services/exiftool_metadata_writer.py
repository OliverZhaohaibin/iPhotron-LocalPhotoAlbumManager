"""Verified ExifTool write-back for user-assigned metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...errors import ExternalToolError
from .location_metadata_service import ExifToolLocationMetadataService


class ExifToolMetadataWriter:
    """Write GPS metadata and verify the original file now contains it."""

    _GPS_TOLERANCE = 1e-4

    def __init__(self, metadata_service: ExifToolLocationMetadataService | None = None) -> None:
        self._metadata = metadata_service or ExifToolLocationMetadataService()

    def write_location(
        self,
        path: Path,
        *,
        latitude: float,
        longitude: float,
        is_video: bool,
        existing_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._metadata.write_gps_metadata(
            path,
            latitude=float(latitude),
            longitude=float(longitude),
            is_video=bool(is_video),
        )
        refreshed = self._metadata.read_back_metadata(
            path,
            is_video=bool(is_video),
            existing_metadata=existing_metadata,
        )
        gps = refreshed.get("gps")
        if not self._gps_matches(gps, latitude=float(latitude), longitude=float(longitude)):
            raise ExternalToolError(
                "ExifTool write completed but GPS metadata could not be verified in the original file"
            )
        return refreshed

    def verify_location(
        self,
        path: Path,
        *,
        latitude: float,
        longitude: float,
        is_video: bool,
        existing_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        refreshed = self._metadata.read_back_metadata(
            path,
            is_video=bool(is_video),
            existing_metadata=existing_metadata,
        )
        if self._gps_matches(
            refreshed.get("gps"),
            latitude=float(latitude),
            longitude=float(longitude),
        ):
            return refreshed
        return None

    def _gps_matches(
        self,
        gps: object,
        *,
        latitude: float,
        longitude: float,
    ) -> bool:
        if not isinstance(gps, dict):
            return False
        try:
            actual_lat = float(gps.get("lat"))
            actual_lon = float(gps.get("lon"))
        except (TypeError, ValueError):
            return False
        return (
            abs(actual_lat - float(latitude)) <= self._GPS_TOLERANCE
            and abs(actual_lon - float(longitude)) <= self._GPS_TOLERANCE
        )


__all__ = ["ExifToolMetadataWriter"]
